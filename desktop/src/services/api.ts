// Single place that knows how to talk to the backend. Every service file
// imports from here instead of calling fetch() directly, so auth headers,
// error handling, and the base path only ever need to change in one place.
//
// Base path is "/api" — Vite's dev-server proxy (vite.config.ts) forwards
// this to the FastAPI backend. In production/Tauri builds without a dev
// proxy, set VITE_API_BASE in a .env file at the desktop/ root, e.g.:
//   VITE_API_BASE=http://127.0.0.1:8001/api

const BASE = (import.meta as any).env?.VITE_API_BASE || "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {
      /* body wasn't JSON — keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  get: <T>(path: string) => fetch(`${BASE}${path}`).then((r) => handle<T>(r)),

  post: <T>(path: string, body?: unknown) =>
    fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }).then((r) => handle<T>(r)),

  put: <T>(path: string, body?: unknown) =>
    fetch(`${BASE}${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }).then((r) => handle<T>(r)),

  delete: <T>(path: string) =>
    fetch(`${BASE}${path}`, { method: "DELETE" }).then((r) => handle<T>(r)),

  /**
   * POST with a streamed text/event-stream response. Can't use the native
   * EventSource here since it doesn't support POST bodies — this reads the
   * stream manually and calls onEvent for each parsed "data: {...}" line.
   * Returns an AbortController so the caller can cancel mid-stream (Stop
   * Generation button).
   */
  postStream(
    path: string,
    body: unknown,
    onEvent: (raw: string) => void,
    onError?: (err: Error) => void,
    onDone?: () => void
  ): AbortController {
    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${BASE}${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          throw new ApiError(res.status, res.statusText);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by a blank line
          const frames = buffer.split("\n\n");
          buffer = frames.pop() || "";
          for (const frame of frames) {
            const line = frame.split("\n").find((l) => l.startsWith("data:"));
            if (line) onEvent(line.slice(5).trim());
          }
        }
        onDone?.();
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          onDone?.(); // user cancelled — not a real error
          return;
        }
        onError?.(err as Error);
      }
    })();

    return controller;
  },
};