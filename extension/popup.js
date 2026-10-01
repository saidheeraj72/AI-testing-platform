const $ = (id) => document.getElementById(id);
const UI_URL = "http://127.0.0.1:5173";

function showRunning(sessionId) {
  $("form").hidden = true;
  $("running").hidden = false;
  $("open").href = `${UI_URL}/sessions/${sessionId}`;
}

chrome.runtime.sendMessage({ type: "status" }, (status) => {
  if (status?.running) showRunning(status.sessionId);
});

document.querySelectorAll('input[name="mode"]').forEach((radio) =>
  radio.addEventListener("change", () => {
    const explore = radio.value === "explore" && radio.checked;
    $("objective-label").textContent = explore ? "Notes (optional)" : "What should I test on this page?";
  }),
);

$("form").addEventListener("submit", (event) => {
  event.preventDefault();
  $("error").textContent = "";
  $("start").disabled = true;
  const mode = document.querySelector('input[name="mode"]:checked').value;
  chrome.runtime.sendMessage({ type: "start", mode, objective: $("objective").value.trim() }, (response) => {
    $("start").disabled = false;
    if (response?.ok) showRunning(response.sessionId);
    else $("error").textContent = response?.error ?? "Could not start the test.";
  });
});

$("stop").addEventListener("click", () => chrome.runtime.sendMessage({ type: "stop" }, () => window.close()));
