import { useEffect, useState } from "react";
import { chunkText } from "../api";
import type { AgentResponse, Citation } from "../types";

/**
 * A redirect deliberately carries no badge.
 *
 * It used to read "Not verified", which described the machinery rather than the reply and
 * read as a failure. Nothing failed: the assistant checked, could not support an answer,
 * and said so — that refusal is the product working. The sentence already says it plainly,
 * and the amber panel already sets it apart, so a label on top only invited the reader to
 * distrust the one kind of answer that has earned the most trust.
 */
const BADGES: Record<string, string> = {
  answer: "Verified answer",
  rows_answer: "From the records",
  redirect: "",
  scope: "Out of scope",
  clarify: "Which did you mean?",
  smalltalk: "",
  approval_request: "Needs your approval",
};

/** Renders the text with [n] markers turned into clickable citation chips. */
function CitedText({ text }: { text: string }) {
  return <p>{text}</p>;
}

/**
 * The preview popup. Everything a reader might want to check lives behind one of these:
 * the rows an answer came from, or the passage a citation points at.
 */
function Preview({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // The page behind must not scroll while a preview is over it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  return (
    <div className="preview-backdrop" onClick={onClose} role="presentation">
      <div
        className="preview"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="preview-head">
          <div>
            <h3>{title}</h3>
            {subtitle && <div className="crumb">{subtitle}</div>}
          </div>
          <button className="preview-close" onClick={onClose} aria-label="Close preview">
            ×
          </button>
        </header>
        <div className="preview-body">{children}</div>
      </div>
    </div>
  );
}

function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<Citation | null>(null);
  const [body, setBody] = useState<string>("");
  const [loading, setLoading] = useState(false);

  async function show(citation: Citation) {
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
        <Preview title={open.title} subtitle={open.breadcrumb} onClose={() => setOpen(null)}>
          <pre className="source-text">{loading ? "Loading source…" : body}</pre>
        </Preview>
      )}
    </>
  );
}

/**
 * The rows behind an answer, behind a button.
 *
 * They used to sit open under every reply. The answer already states the figures in
 * plain words — the table is what confirms them, and confirmation is something a reader
 * asks for rather than something that should crowd out the sentence they came for.
 */
function Evidence({ response }: { response: AgentResponse }) {
  const [open, setOpen] = useState(false);
  if (!response.rows.length) return null;

  const columns = response.columns.length ? response.columns : Object.keys(response.rows[0]);
  const shown = response.rows.slice(0, 100);
  const count = response.rows.length;
  const label = `${count} row${count === 1 ? "" : "s"}`;

  return (
    <>
      <button className="source-btn" onClick={() => setOpen(true)}>
        <span aria-hidden="true">◧</span> Source
        <span className="source-btn-count">{label}</span>
      </button>
      {open && (
        <Preview
          title="Evidence"
          subtitle={`${label} returned from the platform`}
          onClose={() => setOpen(false)}
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c}>{c.replace(/_/g, " ")}</th>
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
          {count > shown.length && (
            <div className="sql">…and {count - shown.length} more rows</div>
          )}
          {response.executed_sql && (
            <div className="sql-block">
              <div className="crumb">The query that produced these rows</div>
              <div className="sql">{response.executed_sql}</div>
            </div>
          )}
        </Preview>
      )}
    </>
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
