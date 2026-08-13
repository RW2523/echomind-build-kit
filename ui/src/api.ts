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
  // A 200 of text/html is the dev server's SPA fallback answering a path its proxy does
  // not forward. Left to JSON.parse it surfaces as `Unexpected token '<'`, which sends
  // the reader looking at the API for a fault that is in vite.config.ts.
  const kind = response.headers.get("content-type") ?? "";
  if (!kind.includes("json")) {
    const path = new URL(response.url, location.origin).pathname;
    throw new Error(
      `${path} returned ${kind || "no content type"} instead of JSON — the dev server is ` +
        `probably not proxying ${path.split("/")[1] ?? path} to the API.`,
    );
  }
  return response.json() as Promise<T>;
}

export async function demoUsers(): Promise<DemoUser[]> {
  const r = await fetch("/demo/users");
  if (!r.ok) return [];
  return (await r.json()).users;
}

/**
 * Sign in as a demo identity.
 *
 * The endpoint returns the handle beside the user rather than inside it, so the `user`
 * object it sends has no `handle` of its own. Everything the UI keys by identity — the
 * "signed in as" highlight in the rail, that person's openers, the remembered identity
 * and their saved thread — reads `user.handle`, so taking the body's `user` at face
 * value silently keys all of it on `undefined`: nobody looks signed in, everybody gets
 * the same openers, and a refresh loses both the identity and the conversation. The
 * handle is folded in here, at the one place that knows both halves of the response.
 */
export async function loginAs(handle: string): Promise<{ token: string; user: DemoUser }> {
  const r = await fetch(`/demo/login/${handle}`, { method: "POST" });
  const body = await json<{ token: string; handle?: string; user: DemoUser }>(r);
  return { token: body.token, user: { ...body.user, handle: body.handle ?? handle } };
}

/**
 * Stream a turn. `onStatus` and `onToken` render progressively; `onFinal` carries the
 * complete, verified payload — citations and gate results only exist there, so nothing
 * is ever shown as sourced before the checks have run.
 *
 * `signal` aborts the request. What that does and does not do is worth being exact
 * about, because the UI has to say it accurately: the HTTP stream ends immediately and
 * nothing further is delivered, but /chat/stream has no cancel of its own and the turn
 * is already running in a worker thread the server cannot interrupt, so the lookup
 * finishes and its checkpoint is written. What cannot happen either way is a write:
 * every write tool returns a pending action and executes only after an approval that
 * this path never reaches.
 */
export async function streamChat(
  message: string,
  threadId: string | null,
  handlers: {
    onStatus?: (stage: string) => void;
    onToken?: (text: string) => void;
    onFinal: (response: AgentResponse) => void;
    onError?: (message: string) => void;
    /** Called instead of onError when the caller aborted, so a deliberate stop is not
     *  reported to the reader as a failure. */
    onAborted?: () => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  try {
    const response = await fetch("/chat/stream", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ message, thread_id: threadId }),
      signal,
    });

    if (!response.ok || !response.body) {
      handlers.onError?.(`Request failed (${response.status})`);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    /** Whether the stream said anything conclusive before it closed. */
    let settled = false;

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
        else if (event === "final") {
          settled = true;
          handlers.onFinal(payload as AgentResponse);
        } else if (event === "error") {
          settled = true;
          handlers.onError?.(payload.message);
        }
      }
    }

    // A stream that closes without a final payload and without an error leaves the turn
    // marked "streaming" for good: a stage trail that never resolves, under a composer
    // that has gone back to normal. A proxy timing the connection out is enough to do it.
    // Ending on a sentence is not a worse outcome than the truth — it is the truth.
    if (!settled && !signal?.aborted) {
      handlers.onError?.(
        "The connection closed before the answer arrived. Nothing was written — ask again.",
      );
    }
  } catch (e) {
    // A dropped connection used to escape this function and reject the caller's await,
    // which left `sending` true forever: the composer stayed disabled and the only way
    // back was a reload. Every ending now leaves through a handler.
    if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      handlers.onAborted?.();
      return;
    }
    handlers.onError?.(e instanceof Error ? e.message : "The connection to the assistant failed.");
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

/** What executing a write tool produced. Documents carry the rows they were built from. */
export type ActionResult = {
  created?: string;
  format?: string;
  filename?: string;
  records?: Record<string, unknown>[];
  record_count?: number;
  records_truncated?: boolean;
  [key: string]: unknown;
};

export async function decideAction(
  actionId: string,
  decision: "approve" | "decline",
): Promise<{ status: string; chat?: AgentResponse; result?: ActionResult }> {
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

/**
 * The run row wraps its scores in `metrics` — unlike /admin/summary, which returns the
 * metrics object directly. Unwrap here so callers only ever see the scores.
 */
export async function adminEvals(): Promise<{ latest: Record<string, unknown> | null }> {
  const r = await fetch("/admin/evals", { headers: headers() });
  const body = await json<{
    latest: { ran_at?: string; metrics?: Record<string, unknown> } | null;
  }>(r);
  if (!body.latest) return { latest: null };
  return { latest: { ...(body.latest.metrics ?? {}), ran_at: body.latest.ran_at } };
}


/** Fetch a generated document and save it.
 *
 * It used to be a plain <a href>, which cannot carry the Authorization header this API
 * requires — so every download was rejected and the button looked broken. The bytes have
 * to come through fetch, become a blob, and be handed to a click we make ourselves.
 */
/** The rows behind an already-executed action.
 *
 * The approval response carries them, but only for the tab that clicked approve. After a
 * reload the card is rebuilt from the thread and the evidence would be gone — which is
 * the wrong direction for a record that exists precisely to be checked later.
 */
export async function actionRecords(actionId: string): Promise<ActionResult | null> {
  const r = await fetch(`/actions/${actionId}`, { headers: headers() });
  if (!r.ok) return null;
  const body = await r.json();
  return (body?.action?.result ?? null) as ActionResult | null;
}

export async function downloadDocument(actionId: string): Promise<void> {
  const response = await fetch(`/actions/${actionId}/document`, { headers: headers() });
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? "That document is no longer available."
        : "Could not download the document.",
    );
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const named = /filename="?([^";]+)"?/.exec(disposition);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = named ? named[1] : `${actionId}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked on the next tick: Safari has not finished reading the blob synchronously.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}


/** A document on the shelf — the same shelf the retriever reads. */
export type LibraryDoc = {
  doc_id: string;
  title: string;
  version: string | null;
  visibility: "public" | "lab" | "private";
  scope: string;
  chunks: number;
  characters: number;
  source_path: string | null;
  updated_at: string | null;
};

export type LibraryDocDetail = Omit<LibraryDoc, "chunks" | "characters"> & {
  sections: { ord: number; breadcrumb: string | null; text: string }[];
};

export async function libraryList(): Promise<{
  documents: LibraryDoc[];
  total: number;
  chunks: number;
}> {
  const r = await fetch("/library", { headers: headers() });
  return json(r);
}

export async function libraryDocument(docId: string): Promise<LibraryDocDetail> {
  const r = await fetch(`/library/${encodeURIComponent(docId)}`, { headers: headers() });
  return json(r);
}


/* --- the data & tools console ---------------------------------------------------- */

/** One role's hold on one relation, as Postgres reports it. */
export type Grant = {
  role: string;
  privileges: string[];
  /** Privileges granted on named columns only — the whole-table view does not carry these. */
  column_privileges: { privilege: string; columns: string[] }[];
};

export type Relation = {
  name: string;
  kind: "table" | "view";
  rows: number | null;
  grants: Grant[];
  browsable: boolean;
  /** Why the row viewer will not page it. Null when it will. */
  not_browsable: string | null;
};

export type Space = {
  schema: string;
  /** COMMENT ON SCHEMA, straight from the database. Null when the schema carries none. */
  purpose: string | null;
  relations: Relation[];
  relation_count: number;
  row_total: number;
  readable_by: string[];
};

export type SpacesResponse = {
  spaces: Space[];
  roles_queried: string[];
  grants_source: string;
};

export type ToolFacts = {
  number: number;
  name: string;
  write: boolean;
  tier: string;
  min_role: string;
  purpose: string;
  subjects: string[];
  /** What the tool returns, field by field, in plain English. */
  reads: Record<string, string>;
  scope: string | null;
  route: "data" | "action";
  /** How a question reaches it: phrases point at this tool, subject words widen the net. */
  asked_as: { how: string; phrases: string[]; subject_words: Record<string, string[]> };
  catalogued: boolean;
};

export type ViewFacts = {
  name: string;
  purpose: string;
  subjects: string[];
  min_role: string;
  scope: string;
  reads: Record<string, string>;
};

export type ToolsResponse = {
  tools: ToolFacts[];
  total: number;
  read: number;
  write: number;
  subjects: string[];
  views: ViewFacts[];
};

export type Stage = {
  key: string;
  name: string;
  where: string;
  what: string;
  facts: Record<string, unknown>;
  refuses: { reason: string; says: string }[];
};

export type PipelineResponse = {
  stages: Stage[];
  refusal_points: string[];
  write_path: { what: string; tools: string[] };
};

export type RowsResponse = {
  schema: string;
  relation: string;
  kind: "table" | "view";
  columns: { name: string; type: string }[];
  rows: Record<string, unknown>[];
  total: number;
  returned: number;
  limit: number;
  offset: number;
  cap: number;
  cap_note: string;
  ordered_by: string[];
};

export async function dataSpaces(): Promise<SpacesResponse> {
  const r = await fetch("/dataspaces", { headers: headers() });
  return json(r);
}

export async function dataTools(): Promise<ToolsResponse> {
  const r = await fetch("/dataspaces/tools", { headers: headers() });
  return json(r);
}

export async function dataPipeline(): Promise<PipelineResponse> {
  const r = await fetch("/dataspaces/pipeline", { headers: headers() });
  return json(r);
}

export async function relationRows(
  schema: string,
  relation: string,
  offset = 0,
  limit = 50,
): Promise<RowsResponse> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const r = await fetch(
    `/dataspaces/rows/${encodeURIComponent(schema)}/${encodeURIComponent(relation)}?${query}`,
    { headers: headers() },
  );
  return json(r);
}
