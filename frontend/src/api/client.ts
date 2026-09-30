// The API only accepts requests carrying the startup token (see backend/app/api/security.py).
export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765";
const TOKEN: string = import.meta.env.VITE_API_TOKEN ?? "";

export const hasToken = TOKEN !== "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: init.method ?? "GET",
      headers: {
        "X-AI-Tester-Token": TOKEN,
        ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError(0, `Cannot reach the AI Tester API at ${API_URL}.`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  return data as T;
}

/** Evidence files need the token too, so they are fetched as blobs rather than linked directly. */
export async function fetchFile(sessionId: string, path: string): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}/files/${path}`, {
    headers: { "X-AI-Tester-Token": TOKEN },
  });
  if (!res.ok) throw new ApiError(res.status, `Could not load ${path}`);
  return res.blob();
}

export function eventsSocketUrl(sessionId: string): string {
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/api/sessions/${sessionId}/events?token=${encodeURIComponent(TOKEN)}`;
}
