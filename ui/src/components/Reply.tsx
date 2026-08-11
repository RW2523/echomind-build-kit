import { useState } from "react";
import { chunkText } from "../api";
import type { AgentResponse, Citation } from "../types";

const BADGES: Record<string, string> = {
  answer: "Verified answer",
  rows_answer: "From the records",
  redirect: "Not verified",
  scope: "Out of scope",
  clarify: "Which did you mean?",
  smalltalk: "",
  approval_request: "Needs your approval",
};

/** Renders the text with [n] markers turned into clickable citation chips. */
function CitedText({ text }: { text: string }) {
  return <p>{text}</p>;
}

function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<Citation | null>(null);
  const [body, setBody] = useState<string>("");
  const [loading, setLoading] = useState(false);

  async function show(citation: Citation) {
    if (open?.chunk_id === citation.chunk_id) {
      setOpen(null);
      return;
    }
    setOpen(citation);
    setLoading(true);
    try {
      const chunk = await chunkText(citation.chunk_id);
      setBody(chunk.text);
    } catch {
      setBody("This source is no longer available to you.");
    } finally {
      setLoading(false);
    }
  }

  if (!citations.length) return null;
  return (
    <>
      <div className="chips">
        {citations.map((c) => (
          <button key={c.chunk_id} className="chip" onClick={() => show(c)}>
            [{c.index}] {c.title}
          </button>
        ))}
      </div>
      {open && (
        <div className="source-panel">
          <div className="crumb">{open.breadcrumb}</div>
          <pre>{loading ? "Loading source…" : body}</pre>
        </div>
      )}
    </>
  );
}

function Evidence({ response }: { response: AgentResponse }) {
  if (!response.rows.length) return null;
  const columns = response.columns.length ? response.columns : Object.keys(response.rows[0]);
  const shown = response.rows.slice(0, 25);
  return (
    <details className="evidence" open={response.rows.length <= 6}>
      <summary>
        Evidence — {response.rows.length} row{response.rows.length === 1 ? "" : "s"} returned
      </summary>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i}>
                {columns.map((c) => (
                  <td key={c}>{String(row[c] ?? "")}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {response.rows.length > shown.length && (
        <div className="sql">…and {response.rows.length - shown.length} more rows</div>
      )}
      {response.executed_sql && <div className="sql">{response.executed_sql}</div>}
    </details>
  );
}

export function Reply({ response }: { response: AgentResponse }) {
  const style =
    response.response_type === "redirect"
      ? "redirect"
      : response.response_type === "scope"
        ? "scope"
        : response.response_type === "clarify"
          ? "clarify"
        : response.response_type === "approval_request"
          ? "approval"
          : "";
  const badge = BADGES[response.response_type] ?? "";

  return (
    <div className={`reply ${style}`}>
      {badge && <div className="badge">{badge}</div>}
      <CitedText text={response.text} />
      <Citations citations={response.citations} />
      <Evidence response={response} />
    </div>
  );
}
