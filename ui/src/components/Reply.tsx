import { useEffect, useRef, useState } from "react";
import { chunkText } from "../api";
import { asCard } from "../card";
import { clarifyOptions } from "../clarify";
import type { AgentResponse, Citation } from "../types";
import { CloseIcon, ShieldIcon } from "./icons";
import { ResultCard } from "./ResultCard";

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
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // The page behind must not scroll while a preview is over it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // aria-modal="true" tells a screen reader nothing outside this dialog exists, so focus
    // has to actually be inside it — otherwise the announcement and the caret disagree and
    // the first Tab walks the page behind. Focus returns to whatever opened it on close.
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
      opener?.focus?.();
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
          <button
            ref={closeRef}
            className="preview-close"
            onClick={onClose}
            aria-label="Close preview"
          >
            <CloseIcon />
          </button>
        </header>
        <div className="preview-body">{children}</div>
      </div>
    </div>
  );
}

/**
 * The answer's prose, with every [n] marker turned into a control that opens the source
 * it points at.
 *
 * The marker is where the reader's doubt actually lands — mid-sentence, on the clause
 * carrying the claim. Leaving it as inert text and putting the only affordance in a chip
 * row underneath asks them to remember which number they were suspicious of.
 */
/**
 * The two inline marks the generator actually emits: `**bold**` around the value a
 * question turned on, and `` `code` `` around an identifier.
 *
 * Nothing else is treated as markup. A reply is prose composed around tool values, not a
 * document, and the more of markdown a renderer accepts the more likely it is to eat a
 * character that belonged to a fact — an asterisk in a footnote, an underscore inside
 * `ACC_A1`. Only the delimiters are removed; every other character reaches the screen
 * exactly as the tool produced it. Left as plain text, the marks show up raw, and the
 * demo's own headline answer reads "**HELIOTROPE-7741**".
 */
const INLINE_MARK = /\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;

function inlineMarks(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE_MARK)) {
    const at = match.index ?? 0;
    if (at > cursor) nodes.push(text.slice(cursor, at));
    if (match[1] !== undefined) {
      nodes.push(<strong key={`${keyPrefix}s${at}`}>{match[1]}</strong>);
    } else {
      nodes.push(<code key={`${keyPrefix}c${at}`}>{match[2]}</code>);
    }
    cursor = at + match[0].length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function CitedText({ text, citations, onOpen }: {
  text: string;
  citations: Citation[];
  onOpen: (citation: Citation) => void;
}) {
  const byIndex = new Map(citations.map((c) => [c.index, c]));
  const parts = text.split(/(\[\d+\])/g);

  return (
    <p className="reply-text">
      {parts.map((part, i) => {
        const match = /^\[(\d+)\]$/.exec(part);
        const citation = match ? byIndex.get(Number(match[1])) : undefined;
        // An index with no citation behind it stays plain text rather than becoming a
        // button that opens nothing.
        if (!citation) return <span key={i}>{inlineMarks(part, `${i}-`)}</span>;
        return (
          <button
            key={i}
            className="marker"
            onClick={() => onOpen(citation)}
            aria-label={`Open source ${citation.index}: ${citation.title}`}
          >
            {citation.index}
          </button>
        );
      })}
    </p>
  );
}

function Sources({ text, citations }: { text: string; citations: Citation[] }) {
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

  return (
    <>
      <CitedText text={text} citations={citations} onOpen={show} />
      {citations.length > 0 && (
        <div className="chips">
          {citations.map((c) => (
            <button key={c.chunk_id} className="chip" onClick={() => show(c)}>
              <span className="chip-index">{c.index}</span>
              {c.title}
            </button>
          ))}
        </div>
      )}
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
 * asks for rather than something that should crowd out the sentence they came for. The
 * same holds once a card is drawn: the card is still a rendering, and the rows stay one
 * click away from it.
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
        <ShieldIcon /> Source
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

/**
 * The options of a clarify turn as buttons that ask the question for the user.
 *
 * The button sends the option verbatim rather than an id: the next turn is a normal
 * message through the normal graph, so nothing here can smuggle a selection past the
 * permission checks that a typed question would have gone through.
 */
function Options({ text, onSend }: { text: string; onSend: (message: string) => void }) {
  const options = clarifyOptions(text);
  if (!options.length) return null;

  return (
    <div className="options">
      {options.map((option) => (
        <button key={option} className="option" onClick={() => onSend(option)}>
          {option}
        </button>
      ))}
    </div>
  );
}

const PANEL: Record<string, string> = {
  redirect: "reply--redirect",
  scope: "reply--scope",
  clarify: "reply--clarify",
  approval_request: "reply--approval",
  smalltalk: "reply--plain",
};

export function Reply({
  response,
  onSend,
}: {
  response: AgentResponse;
  onSend?: (message: string) => void;
}) {
  const panel = PANEL[response.response_type] ?? "";
  const badge = BADGES[response.response_type] ?? "";
  const card = asCard(response.card);

  return (
    <div className={`reply ${panel}`}>
      {badge && <div className="badge">{badge}</div>}
      <Sources text={response.text} citations={response.citations} />
      {response.response_type === "clarify" && onSend && (
        <Options text={response.text} onSend={onSend} />
      )}
      {card && <ResultCard card={card} />}
      <Evidence response={response} />
    </div>
  );
}
