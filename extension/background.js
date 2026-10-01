// AI Tester extension: relays Chrome DevTools Protocol between one tab and the local AI Tester API.
// The API drives the tab with the same agent it uses for its own browser. Chrome shows its
// "is debugging this browser" bar while a test runs.

const DEFAULTS = { apiUrl: "http://127.0.0.1:8765", token: "" };

let relay = null; // { socket, tabId, sessionId }

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(["apiUrl", "token"])) };
}

/** Start a test of a tab: create the session, then connect the relay. Returns { sessionId, uiUrl }. */
async function startTest({ objective = "", mode = "objective", tabId } = {}) {
  if (relay) throw new Error("A test is already running in this browser. Stop it first.");
  const { apiUrl, token } = await settings();
  if (!token) throw new Error("Set the API token in the extension options first.");
  const tab = tabId ? await chrome.tabs.get(tabId) : (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
  if (!tab?.url?.startsWith("http")) throw new Error("Open a web page (http or https) to test it.");

  const res = await fetch(`${apiUrl}/api/tab-sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-AI-Tester-Token": token },
    body: JSON.stringify({ url: tab.url, objective, mode }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `API error ${res.status}`);

  connectRelay(`${apiUrl.replace(/^http/, "ws")}${data.relay_path}?token=${encodeURIComponent(token)}`, tab.id,
               data.session_id);
  return { sessionId: data.session_id };
}

function connectRelay(url, tabId, sessionId) {
  const socket = new WebSocket(url);
  relay = { socket, tabId, sessionId, attached: false };
  socket.onmessage = async (event) => {
    const message = JSON.parse(event.data);
    try {
      const result = await handle(message);
      socket.send(JSON.stringify({ id: message.id, result }));
    } catch (error) {
      socket.send(JSON.stringify({ id: message.id, error: { message: String(error?.message ?? error) } }));
    }
  };
  socket.onclose = () => stopRelay();
}

async function handle(message) {
  const { tabId } = relay;
  if (message.method === "attachToTab") {
    await chrome.debugger.attach({ tabId }, "1.3");
    relay.attached = true;
    const { targetInfo } = await chrome.debugger.sendCommand({ tabId }, "Target.getTargetInfo");
    return { targetInfo };
  }
  if (message.method === "forwardCDPCommand") {
    const { method, params, sessionId } = message.params;
    const target = sessionId ? { tabId, sessionId } : { tabId };
    return (await chrome.debugger.sendCommand(target, method, params)) ?? {};
  }
  throw new Error(`unknown relay method ${message.method}`);
}

async function stopRelay() {
  if (!relay) return;
  const { tabId, attached, socket } = relay;
  relay = null;
  if (attached) await chrome.debugger.detach({ tabId }).catch(() => {});
  if (socket.readyState === WebSocket.OPEN) socket.close();
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!relay || source.tabId !== relay.tabId || relay.socket.readyState !== WebSocket.OPEN) return;
  relay.socket.send(JSON.stringify({ method: "forwardCDPEvent", params: { method, params, sessionId: source.sessionId } }));
});

// The user closed the debugging bar or the tab: end the relay; the API records the session as stopped.
chrome.debugger.onDetach.addListener((source) => {
  if (relay && source.tabId === relay.tabId) {
    relay.attached = false;
    stopRelay();
  }
});

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.type === "start") {
    startTest(request).then((r) => sendResponse({ ok: true, ...r }), (e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (request.type === "status") {
    sendResponse({ running: !!relay, sessionId: relay?.sessionId ?? null });
  }
  if (request.type === "stop") {
    stopRelay().then(() => sendResponse({ ok: true }));
    return true;
  }
});

// For automated tests of the extension.
globalThis.aiTester = { startTest, stopRelay, status: () => ({ running: !!relay, sessionId: relay?.sessionId ?? null }) };
