import { useState } from "react";
import { actionRecords, decideAction, downloadDocument, type ActionResult } from "../api";
import { Preview } from "./Preview";
import type { PendingAction } from "../types";

interface Props {
  action: PendingAction;
  decision?: { status: string; text?: string; actionId: string };
  /** A later proposal in this conversation is still waiting for a decision too. */
  earlier?: boolean;
  onDecided: (status: string, text: string | undefined, actionId: string) => void;
}

/** Objects and arrays are shown as JSON rather than "[object Object]", which hides
 *  exactly the nested payload a reader is being asked to vouch for. */
function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** The confirmation ends with the route that serves the generated file, if there is one. */
function downloadUrlIn(text: string | undefined): string | null {
  const match = /\/actions\/[a-zA-Z0-9-]+\/document/.exec(text ?? "");
  return match ? match[0] : null;
}

/** …and the sentence reads better without the raw path once it is a button. */
function stripDownloadUrl(text: string): string {
  return text.replace(/\s*Download it here:\s*\/actions\/[a-zA-Z0-9-]+\/document\.?/, "").trim();
}

export function ApprovalCard({ action, decision, earlier, onDecided }: Props) {
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [result, setResult] = useState<ActionResult | null>(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [loadingSource, setLoadingSource] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(choice: "approve" | "decline") {
    setBusy(choice);
    setError(null);
    try {
      const outcome = await decideAction(action.action_id, choice);
      // Kept so the reader can open the rows the document was built from: a statement of
      // charges is only as trustworthy as the ledger behind it, and that ledger is one
      // click away rather than something they have to take on faith.
      setResult(outcome.result ?? null);
      onDecided(outcome.status, outcome.chat?.text, action.action_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not record that decision.");
    } finally {
      setBusy(null);
    }
  }

  const entries = Object.entries(action.payload ?? {});
  const locked = Boolean(decision);
  const executed = decision?.status === "executed";

  /* An amended proposal ("actually make it 3 hours") arrives as a new action; the one it
     amends stays pending, and approving it writes the older booking. Nothing here can
     retire it — that decision belongs to the server, which still holds it as pending —
     so the card says plainly which proposal it is. It keeps both buttons: an earlier
     proposal is still the user's to approve or, more usefully, to decline. */
  const superseded = Boolean(earlier) && !locked;

  return (
    <section
      className={`approval${superseded ? " approval--earlier" : ""}`}
      aria-label={superseded ? "Earlier pending action" : "Pending action"}
    >
      <header className="approval-head">
        <span className="approval-flag">
          {superseded ? "Earlier proposal · still live" : "Awaiting your approval"}
        </span>
        <span className="approval-kind">{action.kind ?? action.tool}</span>
        <span className="approval-id">{action.action_id}</span>
      </header>

      <p className="approval-preview">{action.payload_preview}</p>

      {superseded && (
        <p className="approval-earlier-note">
          There is a newer proposal below, also still waiting. Approving this one does
          exactly what it says here — check that is what you meant.
        </p>
      )}

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
          {decision!.text ? <span> — {stripDownloadUrl(decision!.text)}</span> : null}
          {/* A generated document is only finished when the reader can open it. The
              confirmation carries the route that serves the file; rendering it as a link
              is the difference between being told a document exists and having it. */}
          {executed && downloadUrlIn(decision!.text) ? (
            <button
              type="button"
              className="approval-download"
              disabled={downloading}
              onClick={async () => {
                setDownloading(true);
                setError(null);
                try {
                  await downloadDocument(action.action_id);
                } catch (e) {
                  setError(e instanceof Error ? e.message : "Could not download it.");
                } finally {
                  setDownloading(false);
                }
              }}
            >
              {downloading ? "Preparing…" : "Download document"}
            </button>
          ) : null}
          {executed && (result === null || (result.records?.length ?? 0) > 0) ? (
            <button
              type="button"
              className="source-btn"
              // Not `disabled`: see Library.tsx — disabling a focused button blurs it, and
              // the preview then has nothing to hand focus back to when it closes.
              aria-busy={loadingSource}
              onClick={async () => {
                if (loadingSource) return;
                if (result?.records?.length) {
                  setSourceOpen(true);
                  return;
                }
                setLoadingSource(true);
                setError(null);
                const fetched = await actionRecords(action.action_id);
                setLoadingSource(false);
                // An empty result is an answer: the button stops offering evidence that
                // is not there rather than opening an empty table. Saying so matters —
                // a button that does nothing when clicked reads as broken.
                setResult(fetched ?? { records: [] });
                if (fetched?.records?.length) setSourceOpen(true);
                else setError("The records behind this document are no longer available.");
              }}
            >
              Source
              {result?.record_count ? (
                <span className="source-btn-count">
                  {result.record_count} record{result.record_count === 1 ? "" : "s"}
                </span>
              ) : null}
            </button>
          ) : null}
          {error && <div className="approval-error">{error}</div>}
          <div className="approval-id">action {decision!.actionId} · recorded in the audit log</div>
        </div>
      )}

      {sourceOpen && result?.records?.length ? (
        <Preview
          title="Records used"
          subtitle={`${result.record_count} record${
            result.record_count === 1 ? "" : "s"
          } from the platform went into this document`}
          onClose={() => setSourceOpen(false)}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {Object.keys(result.records[0]).map((column) => (
                    <th key={column}>{column.replace(/_/g, " ")}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.records.map((row, i) => (
                  <tr key={i}>
                    {Object.keys(result.records![0]).map((column) => (
                      <td key={column}>{String(row[column] ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.records_truncated ? (
            <div className="sql">
              Showing the first {result.records.length} of {result.record_count}.
            </div>
          ) : null}
        </Preview>
      ) : null}
    </section>
  );
}
