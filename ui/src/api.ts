import type { AgentResponse, DemoUser, UploadRecord } from "./types";

let token = "";

export function setToken(value: string) {
  token = value;
}

function headers(extra: Record<string, string> = {}) {
  return { Authorization: `Bearer ${token}`, ...extra };
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = await response.text();
    }
    const message =
      typeof detail === "object" && detail && "message" in detail
        ? String((detail as { message: unknown }).message)
        : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export async function demoUsers(): Promise<DemoUser[]> {
  const r = await fetch("/demo/users");
  if (!r.ok) return [];
  return (await r.json()).users;
}

export async function loginAs(handle: string): Promise<{ token: string; user: DemoUser }> {
  const r = await fetch(`/demo/login/${handle}`, { method: "POST" });
  return json(r);
}

/**
 * Stream a turn. `onStatus` and `onToken` render progressively; `onFinal` carries the
 * complete, verified payload — citations and gate results only exist there, so nothing
 * is ever shown as sourced before the checks have run.
 */
export async function streamChat(
  message: string,
  threadId: string | null,
  handlers: {
    onStatus?: (stage: string) => void;
    onToken?: (text: string) => void;
    onFinal: (response: AgentResponse) => void;
    onError?: (message: string) => void;
  },
): Promise<void> {
  const response = await fetch("/chat/stream", {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message, thread_id: threadId }),
  });

  if (!response.ok || !response.body) {
    handlers.onError?.(`Request failed (${response.status})`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      const payload = JSON.parse(dataLines.join("\n"));
      if (event === "status") handlers.onStatus?.(payload.stage);
      else if (event === "token") handlers.onToken?.(payload.text);
      else if (event === "final") handlers.onFinal(payload as AgentResponse);
      else if (event === "error") handlers.onError?.(payload.message);
    }
  }
}

export async function getThread(threadId: string): Promise<{
  thread_id: string;
  message: string | null;
  route: string | null;
  response: AgentResponse | null;
  awaiting_approval: boolean;
}> {
  const r = await fetch(`/threads/${threadId}`, { headers: headers() });
  return json(r);
}

export async function decideAction(
  actionId: string,
  decision: "approve" | "decline",
): Promise<{ status: string; chat?: AgentResponse }> {
  const r = await fetch(`/actions/${actionId}/${decision}`, {
    method: "POST",
    headers: headers(),
  });
  return json(r);
}

export async function chunkText(chunkId: number): Promise<{ text: string; breadcrumb: string }> {
  const r = await fetch(`/tools/chunk/${chunkId}`, { headers: headers() });
  return json(r);
}

export async function listUploads(): Promise<UploadRecord[]> {
  const r = await fetch("/uploads", { headers: headers() });
  return (await json<{ uploads: UploadRecord[] }>(r)).uploads;
}

export async function upload(file: File): Promise<UploadRecord & { note: string }> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch("/uploads", { method: "POST", headers: headers(), body: form });
  return json(r);
}

export async function deleteUpload(docId: string): Promise<void> {
  await fetch(`/uploads/${docId}`, { method: "DELETE", headers: headers() });
}

export async function adminSummary(): Promise<Record<string, unknown>> {
  const r = await fetch("/admin/summary", { headers: headers() });
  return json(r);
}

export async function adminAudit(params: { status?: string; kind?: string }): Promise<{
  actions: Record<string, unknown>[];
  events: Record<string, unknown>[];
  kinds: string[];
  statuses: string[];
}> {
  const query = new URLSearchParams();
  if (params.status) query.set("status", params.status);
  if (params.kind) query.set("kind", params.kind);
  const r = await fetch(`/admin/audit?${query}`, { headers: headers() });
  return json(r);
}

export async function adminTraces(): Promise<{
  sink: string;
  langfuse_url: string | null;
  spans: Record<string, unknown>[];
}> {
  const r = await fetch("/admin/traces?limit=40", { headers: headers() });
  return json(r);
}

export async function adminEvals(): Promise<{ latest: Record<string, unknown> | null }> {
  const r = await fetch("/admin/evals", { headers: headers() });
  return json(r);
}
