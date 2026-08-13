import { useEffect, useMemo, useState } from "react";
import { libraryDocument, libraryList, type LibraryDoc, type LibraryDocDetail } from "../api";
import { Preview } from "./Preview";

/**
 * The shelf, readable before you ask anything of it.
 *
 * Every answer already names its sources, so the corpus was visible one citation at a
 * time and nowhere as a whole — a reader who wanted to know what the assistant had been
 * given had to guess questions until the documents surfaced. This is the same list the
 * retriever reads, filtered by the same predicate: nothing appears here that could not
 * also be cited at you, and nothing that could is hidden.
 */

/** Documents sort into shelves by who they are for and what they are about. */
function shelfOf(doc: LibraryDoc): string {
  if (doc.visibility === "private") return "Yours alone";
  if (doc.visibility === "lab") return `Your lab — ${doc.scope.replace("lab ", "")}`;
  // What a document *is* comes before what it is *about*: "Training Module 12 —
  // Instrument Troubleshooting" is a training module that happens to mention
  // troubleshooting, and testing the subject first filed it under the instrument sheets.
  if (/^safety notice/i.test(doc.title)) return "Safety notices";
  if (/^training module/i.test(doc.title)) return "Training modules";
  if (/^faq/i.test(doc.title)) return "Cross-core FAQs";
  if (/^consumable standard/i.test(doc.title)) return "Consumable standards";
  if (/operating procedure|troubleshooting|maintenance schedule|booking notes/i.test(doc.title))
    return "Instrument documentation";
  return "Facility policies";
}

// Policies first: they are what someone browsing the shelf is usually looking for, and
// the hundred instrument sheets would otherwise bury them.
const SHELF_ORDER = [
  "Facility policies",
  "Safety notices",
  "Training modules",
  "Cross-core FAQs",
  "Consumable standards",
  "Instrument documentation",
];

function shelfRank(name: string): number {
  const known = SHELF_ORDER.indexOf(name);
  if (known >= 0) return known;
  return name.startsWith("Your lab") ? SHELF_ORDER.length : SHELF_ORDER.length + 1;
}

export function Library() {
  const [docs, setDocs] = useState<LibraryDoc[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<LibraryDocDetail | null>(null);
  const [opening, setOpening] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    libraryList()
      .then((r) => live && setDocs(r.documents))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Could not load the library."));
    return () => {
      live = false;
    };
  }, []);

  const shelves = useMemo(() => {
    if (!docs) return [];
    const needle = query.trim().toLowerCase();
    const matching = needle
      ? docs.filter(
          (d) =>
            d.title.toLowerCase().includes(needle) ||
            (d.source_path ?? "").toLowerCase().includes(needle),
        )
      : docs;
    const grouped = new Map<string, LibraryDoc[]>();
    for (const doc of matching) {
      const shelf = shelfOf(doc);
      const existing = grouped.get(shelf);
      if (existing) existing.push(doc);
      else grouped.set(shelf, [doc]);
    }
    return [...grouped.entries()].sort((a, b) => shelfRank(a[0]) - shelfRank(b[0]));
  }, [docs, query]);

  async function preview(doc: LibraryDoc) {
    if (opening) return;  // the row stays enabled for focus's sake, so guard here instead
    setOpening(doc.doc_id);
    setError(null);
    try {
      setOpen(await libraryDocument(doc.doc_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not open that document.");
    } finally {
      setOpening(null);
    }
  }

  const shown = shelves.reduce((n, [, group]) => n + group.length, 0);

  return (
    <div className="library">
      <header className="library-head">
        <div>
          <h2>Resources</h2>
          <p className="library-sub">
            {docs === null
              ? "Reading the shelf…"
              : `${docs.length} document${docs.length === 1 ? "" : "s"} this assistant can
                 answer you from. Everything here is something it could cite.`}
          </p>
        </div>
        <input
          className="library-search"
          type="search"
          placeholder="Filter by title…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Filter documents by title"
        />
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}

      {docs !== null && shown === 0 && (
        <p className="library-empty">
          {query ? `No document matches “${query}”.` : "There is nothing on the shelf yet."}
        </p>
      )}

      {shelves.map(([shelf, group]) => (
        <section className="library-shelf" key={shelf}>
          <h3>
            {shelf}
            <span className="library-count">{group.length}</span>
          </h3>
          <ul className="library-list">
            {group.map((doc) => (
              <li key={doc.doc_id}>
                <button
                  type="button"
                  className="library-item"
                  // Deliberately not `disabled` while the fetch is in flight. Disabling a
                  // focused element blurs it immediately, so by the time the preview
                  // mounts and records what to return focus to, the answer is <body> — and
                  // a keyboard reader who closes the preview is dropped back at the top of
                  // the page instead of the row they opened. aria-busy says the same thing
                  // to a screen reader without moving the caret.
                  aria-busy={opening === doc.doc_id}
                  onClick={() => preview(doc)}
                >
                  <span className="library-title">{doc.title}</span>
                  <span className="library-meta">
                    {doc.version ? <span>v{doc.version}</span> : null}
                    <span>
                      {doc.chunks} section{doc.chunks === 1 ? "" : "s"}
                    </span>
                    <span className={`library-scope scope--${doc.visibility}`}>{doc.scope}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {open && (
        <Preview
          title={open.title}
          subtitle={`${open.version ? `v${open.version} · ` : ""}${open.scope} · ${
            open.sections.length
          } section${open.sections.length === 1 ? "" : "s"}`}
          onClose={() => setOpen(null)}
        >
          {/* The chunks, in order, as they are indexed — not the file on disk. Those two
              can drift, and the preview exists so a reader can check what an answer was
              actually drawn from. */}
          {open.sections.map((section) => (
            <article className="doc-section" key={section.ord}>
              {section.breadcrumb && <div className="crumb">{section.breadcrumb}</div>}
              <p className="doc-text">{section.text}</p>
            </article>
          ))}
          {open.source_path && <div className="sql">ingested from {open.source_path}</div>}
        </Preview>
      )}
    </div>
  );
}
