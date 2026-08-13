export type ResponseType =
  | "answer"
  | "rows_answer"
  | "approval_request"
  | "redirect"
  | "scope"
  | "clarify"
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

/** The six shapes the UI knows how to lay out. Anything else falls back to the table. */
export type CardKind =
  | "invoice"
  | "facilities"
  | "instruments"
  | "booking"
  | "document"
  | "usage";

export type BadgeTone = "ok" | "warn" | "info" | "muted";

export interface CardField {
  label: string;
  value: string;
  emphasis: boolean;
}

export interface CardBadge {
  text: string;
  tone: BadgeTone;
}

export interface CardItem {
  title: string;
  subtitle: string | null;
  meta: string[];
  badges: CardBadge[];
  value: string | null;
}

/**
 * A card is data, not prose: every string in it was produced by a tool. The renderer
 * therefore only ever positions and styles these strings — it never parses a value to
 * reformat it, because a figure the browser rewrote is a figure no tool stands behind.
 */
export interface Card {
  kind: CardKind;
  title: string;
  subtitle: string | null;
  fields: CardField[];
  items: CardItem[];
  footer: string | null;
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
  /** Optional structured rendering of the same facts the rows carry. */
  card?: unknown;
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
  /** Every verification stage the server has announced, oldest first. */
  stages?: string[];
  /** The reader ended the turn before its answer arrived. Not an error: nothing failed,
   *  and the distinction is what stops a deliberate stop from reading as a fault. */
  stopped?: boolean;
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
