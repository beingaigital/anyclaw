import type { KernelEvent, SessionMessage, SessionSummary, PendingInterrupt } from "./types";

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${text}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  listSessions: () =>
    fetch("/api/sessions?limit=50").then((r) => json<{ sessions: SessionSummary[] }>(r)),

  createSession: (initialQuery = "新会话") =>
    fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial_query: initialQuery }),
    }).then((r) => json<{ task_id: string }>(r)),

  getSession: (id: string) =>
    fetch(`/api/sessions/${id}`).then((r) =>
      json<SessionSummary & { pending_interrupt: PendingInterrupt | null }>(r)
    ),

  getMessages: (id: string) =>
    fetch(`/api/sessions/${id}/messages`).then((r) =>
      json<{ messages: SessionMessage[]; token_usage: any }>(r)
    ),

  deleteSession: (id: string) =>
    fetch(`/api/sessions/${id}`, { method: "DELETE" }).then((r) => json<{ ok: boolean }>(r)),

  tools: () => fetch("/api/tools").then((r) => json<any>(r)),
  reloadTools: () => fetch("/api/tools/reload", { method: "POST" }).then((r) => json<any>(r)),
  models: () => fetch("/api/models").then((r) => json<any>(r)),
  profile: () => fetch("/api/profile").then((r) => json<any>(r)),
  reloadProfile: () => fetch("/api/profile/reload", { method: "POST" }).then((r) => json<any>(r)),
  skills: () => fetch("/api/skills").then((r) => json<any>(r)),
  reloadSkills: () => fetch("/api/skills/reload", { method: "POST" }).then((r) => json<any>(r)),
};

/** POST + SSE 消费（EventSource 不支持 POST，用 fetch ReadableStream）。 */
export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (ev: KernelEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (dataLine) {
        try {
          onEvent(JSON.parse(dataLine.slice(6)) as KernelEvent);
        } catch {
          // 忽略损坏帧
        }
      }
    }
  }
}