import { useState } from "react";
import { decideAction } from "../api";
import type { PendingAction } from "../types";

interface Props {
  action: PendingAction;
  decision?: { status: string; text?: string; actionId: string };
  onDecided: (status: string, text: string | undefined, actionId: string) => void;
}

/** Objects and arrays are shown as JSON rather than "[object Object]", which hides
 *  exactly the nested payload a reader is being asked to vouch for. */
function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function ApprovalCard({ action, decision, onDecided }: Props) {
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(choice: "approve" | "decline") {
    setBusy(choice);
    setError(null);
    try {
      const result = await decideAction(action.action_id, choice);
      onDecided(result.status, result.chat?.text, action.action_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not record that decision.");
    } finally {
      setBusy(null);
    }
  }

  const entries = Object.entries(action.payload ?? {});
  const locked = Boolean(decision);
  const executed = decision?.status === "executed";

  return (
    <section className="approval" aria-label="Pending action">
      <header className="approval-head">
        <span className="approval-flag">Awaiting your approval</span>
        <span className="approval-kind">{action.kind ?? action.tool}</span>
        <span className="approval-id">{action.action_id}</span>
      </header>

      <p className="approval-preview">{action.payload_preview}</p>

      {entries.length > 0 && (
        <dl className="approval-kv">
          {entries.map(([key, value]) => (
            <div className="approval-row" key={key}>
              <dt>{key.replace(/_/g, " ")}</dt>
              <dd>{render(value)}</dd>
            </div>
          ))}
        </dl>
      )}

      {!locked && (
        <div className="approval-actions">
          <button
            className="btn btn--go"
            disabled={busy !== null}
            onClick={() => decide("approve")}
          >
            {busy === "approve" ? "Executing…" : "Approve"}
          </button>
          <button
            className="btn btn--quiet"
            disabled={busy !== null}
            onClick={() => decide("decline")}
          >
            {busy === "decline" ? "Declining…" : "Decline"}
          </button>
          <span className="approval-note">Nothing is written until you approve.</span>
          {error && <span className="approval-error">{error}</span>}
        </div>
      )}

      {locked && (
        <div className={`approval-outcome ${executed ? "is-executed" : "is-declined"}`}>
          <strong>{decision!.status}</strong>
          {decision!.text ? <span> — {decision!.text}</span> : null}
          <div className="approval-id">action {decision!.actionId} · recorded in the audit log</div>
        </div>
      )}
    </section>
  );
}
