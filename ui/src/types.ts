export type ResponseType =
  | "answer"
  | "rows_answer"
  | "approval_request"
  | "redirect"
  | "scope"
  | "smalltalk";

export interface Citation {
  index: number;
  doc_id: string;
  breadcrumb: string;
  title: string;
  chunk_id: number;
  score: number;
}

export interface PendingAction {
  action_id: string;
  status: string;
  kind: string | null;
  tool: string;
  payload_preview: string;
  payload: Record<string, unknown>;
  message?: string;
}

export interface AgentResponse {
  response_type: ResponseType;
  text: string;
  citations: Citation[];
  rows: Record<string, unknown>[];
  columns: string[];
  executed_sql: string | null;
  pending_action: PendingAction | null;
  gate: Record<string, unknown> | null;
  faithfulness: Record<string, unknown> | null;
  route: string | null;
  meta: Record<string, unknown>;
  thread_id?: string;
}

export interface DemoUser {
  handle: string;
  id: string;
  name: string;
  role: string;
  lab_ids: string[];
  blurb: string;
}

export interface Turn {
  id: string;
  question: string;
  response?: AgentResponse;
  streaming?: boolean;
  streamedText?: string;
  status?: string;
  error?: string;
  /** Set once the user approves or declines the turn's pending action. */
  decision?: { status: string; text?: string; actionId: string };
}

export interface UploadRecord {
  id: string;
  title: string;
  version: string;
  chunks: number;
  updated_at: string;
}
