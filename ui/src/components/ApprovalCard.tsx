import { useState } from "react";
import { decideAction } from "../api";
import type { PendingAction } from "../types";

interface Props {
  action: PendingAction;
  decision?: { status: string; text?: string; actionId: string };
  onDecided: (status: string, text: string | undefined, actionId: string) => void;
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

  return (
    <div className="approval-card">
      <div className="head">
        <span>{action.kind ?? action.tool} — pending approval</span>
        <span className="aid">{action.action_id}</span>
      </div>

      <dl className="kv">
        <dt>action</dt>
        <dd>{action.payload_preview}</dd>
        {entries.map(([key, value]) => (
          <div key={key} style={{ display: "contents" }}>
            <dt>{key}</dt>
            <dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
          </div>
        ))}
      </dl>

      {!locked && (
        <div className="actions">
          <button className="primary" disabled={busy !== null} onClick={() => decide("approve")}>
            {busy === "approve" ? "Executing…" : "Approve"}
          </button>
          <button className="secondary" disabled={busy !== null} onClick={() => decide("decline")}>
            {busy === "decline" ? "Declining…" : "Decline"}
          </button>
          {error && <span style={{ color: "#a03030", fontSize: 12.5 }}>{error}</span>}
        </div>
      )}

      {locked && (
        <div className={`outcome ${decision!.status}`}>
          <div>
            <strong>{decision!.status}</strong>
            {decision!.text ? ` — ${decision!.text}` : ""}
          </div>
          <div className="aid">action {decision!.actionId} · recorded in the audit log</div>
        </div>
      )}
    </div>
  );
}
