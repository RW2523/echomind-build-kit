import { useEffect, useState } from "react";
import {
  dataPipeline,
  dataSpaces,
  dataTools,
  relationRows,
  type PipelineResponse,
  type Relation,
  type RowsResponse,
  type Space,
  type SpacesResponse,
  type ToolFacts,
  type ToolsResponse,
} from "../api";
import { Preview } from "./Preview";

/**
 * Data & Tools: the architecture, shown rather than asserted.
 *
 * Everything this product claims about itself — the vendor's tables are read and never
 * written, the agent's SQL runs as a role holding SELECT on nine views and nothing else,
 * a question is refused at a named stage by a named check — was until now something a
 * reader had to take on trust or go and read the source for. Every figure on this page
 * comes back from the database or from the live registries on each load, so the page
 * cannot quietly describe last month's system.
 */

const PAGE = 50;

/** Where the row viewer is, if it is open. */
type Viewing = { schema: string; relation: string; offset: number };

function count(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** A relation's readers, shortened to the role name — the prefix is the same on all of them. */
function shortRole(role: string): string {
  return role.replace(/^echomind_/, "");
}

function grantSummary(relation: Relation): string {
  if (!relation.grants.length) return "—";
  return relation.grants
    .map((g) => {
      const columns = g.column_privileges.map(
        (c) => `${c.privilege} (${c.columns.join(", ")})`,
      );
      return `${shortRole(g.role)}: ${[...g.privileges, ...columns].join(", ")}`;
    })
    .join(" · ");
}

export function DataSpaces() {
  const [spaces, setSpaces] = useState<SpacesResponse | null>(null);
  const [tools, setTools] = useState<ToolsResponse | null>(null);
  const [pipeline, setPipeline] = useState<PipelineResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [viewing, setViewing] = useState<Viewing | null>(null);
  const [page, setPage] = useState<RowsResponse | null>(null);
  const [paging, setPaging] = useState(false);
  const [openTool, setOpenTool] = useState<ToolFacts | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([dataSpaces(), dataTools(), dataPipeline()])
      .then(([s, t, p]) => {
        if (!live) return;
        setSpaces(s);
        setTools(t);
        setPipeline(p);
      })
      .catch((e) =>
        live && setError(e instanceof Error ? e.message : "Could not read the data spaces."),
      );
    return () => {
      live = false;
    };
  }, []);

  // The row viewer refetches whenever the window moves, rather than accumulating pages in
  // the client: the cap is a server-side promise about how much is read at once, and a
  // client that stitched pages together behind the modal would quietly break it.
  useEffect(() => {
    if (!viewing) {
      setPage(null);
      return;
    }
    let live = true;
    setPaging(true);
    relationRows(viewing.schema, viewing.relation, viewing.offset, PAGE)
      .then((r) => live && setPage(r))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Could not read the rows."))
      .finally(() => live && setPaging(false));
    return () => {
      live = false;
    };
  }, [viewing]);

  const totalRows = spaces?.spaces.reduce((n, s) => n + s.row_total, 0) ?? 0;

  return (
    <div className="spaces">
      <div className="spaces-inner">
        <header className="spaces-head">
          <h2>Data &amp; tools</h2>
          <p className="spaces-sub">
            How this application's data is segregated and how the agent reaches it. Every
            figure here is read from the database or from the tool registry on load — the
            purposes are the schemas' own COMMENT ON SCHEMA and the grants are Postgres's
            own answer, not a description of them kept beside the code.
          </p>
        </header>

        {error && <div className="error-banner" role="alert">{error}</div>}

        <div className="cards">
          <div className="card">
            <div className="label">Spaces</div>
            <div className="value">{spaces?.spaces.length ?? "—"}</div>
            <div className="sub">one vendor schema, five domain spaces, two ours</div>
          </div>
          <div className="card">
            <div className="label">Relations</div>
            <div className="value">
              {spaces ? spaces.spaces.reduce((n, s) => n + s.relation_count, 0) : "—"}
            </div>
            <div className="sub">{spaces ? `${totalRows.toLocaleString()} rows in total` : ""}</div>
          </div>
          <div className="card">
            <div className="label">Tools</div>
            <div className="value">{tools?.total ?? "—"}</div>
            <div className="sub">
              {tools ? `${tools.read} read · ${tools.write} propose a write` : ""}
            </div>
          </div>
          <div className="card">
            <div className="label">Refusal points</div>
            <div className="value">{pipeline?.refusal_points.length ?? "—"}</div>
            <div className="sub">stages that can decline to answer</div>
          </div>
        </div>

        {/* --- 1. the spaces --- */}

        <div className="section-title">The spaces</div>
        <p className="spaces-note">
          Grants come from <code>{spaces?.grants_source ?? "information_schema"}</code>, asked
          of {(spaces?.roles_queried ?? []).map(shortRole).join(" and ")} separately — Postgres
          shows a session only its own grants, so asking once as the application would have
          reported that the agent's read-only role can reach nothing at all.
        </p>

        {(spaces?.spaces ?? []).map((space) => (
          <SpaceBlock
            key={space.schema}
            space={space}
            onOpen={(relation) =>
              setViewing({ schema: space.schema, relation: relation.name, offset: 0 })
            }
          />
        ))}

        {/* --- 2. the tools --- */}

        <div className="section-title">The tools</div>
        <p className="spaces-note">
          Read from the registry in <code>server/mcp/tools.py</code> on every load and
          described from the source catalogue, so a tool added tomorrow appears here without
          this page being edited. Open one to see what it returns and which words reach it.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th><th>tool</th><th>kind</th><th>needs</th><th>answers about</th>
                <th>reached by</th>
              </tr>
            </thead>
            <tbody>
              {(tools?.tools ?? []).map((tool) => (
                <tr key={tool.name}>
                  <td className="muted-cell">{tool.number}</td>
                  <td>
                    <button className="linkish" onClick={() => setOpenTool(tool)}>
                      {tool.name}
                    </button>
                  </td>
                  <td>
                    <span className={`pill ${tool.write ? "pending" : "executed"}`}>
                      {tool.write ? "write" : "read"}
                    </span>
                  </td>
                  <td>{tool.min_role}</td>
                  <td className="muted-cell">{tool.subjects.join(", ") || "—"}</td>
                  {/* Phrases for a read tool. Nothing keyword-matches its way to a write,
                      so listing its subject words here would imply routing that does not
                      happen; the column says how it is actually reached instead. */}
                  <td className="muted-cell">
                    {tool.write
                      ? "the action route"
                      : tool.asked_as.phrases.slice(0, 3).join(", ") || "subject words only"}
                  </td>
                </tr>
              ))}
              {!tools?.tools.length && (
                <tr><td colSpan={6} className="muted-cell">Reading the registry…</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="section-title">The relations behind tool 11</div>
        <p className="spaces-note">
          Not tools. These are reachable only through <code>run_readonly_sql</code>, and only
          when the validator recognises the name written out in full. Mostly views;{" "}
          <code>policy.statements</code> is a real table, which is the point of it — the
          rules are data a decision can cite, not prose it has to interpret.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>view</th><th>needs</th><th>reach</th><th>what it answers</th></tr>
            </thead>
            <tbody>
              {(tools?.views ?? []).map((view) => (
                <tr key={view.name}>
                  <td className="mono-cell">{view.name}</td>
                  <td>{view.min_role}</td>
                  <td className="muted-cell">{view.scope}</td>
                  <td>{view.purpose}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* --- 3. the path a question takes --- */}

        <div className="section-title">The path a question takes</div>
        <p className="spaces-note">
          The stages are described here; the numbers inside them are read from the modules
          that enforce them, so the ceiling and the allow-list below are the ones applied to
          the next question asked. A stage tinted amber can refuse.
        </p>
        <ol className="stage-list">
          {(pipeline?.stages ?? []).map((stage, i) => (
            <li
              key={stage.key}
              className={`stage-step${stage.refuses.length ? " stage-step--refuses" : ""}`}
            >
              <div className="stage-step-head">
                <span className="stage-step-n">{i + 1}</span>
                <div>
                  <div className="stage-step-name">{stage.name}</div>
                  <div className="crumb">{stage.where}</div>
                </div>
              </div>
              <p className="stage-step-what">{stage.what}</p>
              {Object.entries(stage.facts).length > 0 && (
                <dl className="fact-list">
                  {Object.entries(stage.facts).map(([key, value]) => (
                    <div className="fact" key={key}>
                      <dt>{key.replace(/_/g, " ")}</dt>
                      <dd>
                        {Array.isArray(value) ? value.join(", ") : String(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
              {stage.refuses.map((refusal) => (
                <div className="stage-refusal" key={refusal.reason}>
                  <span className="tone tone--warn">{refusal.reason}</span>
                  <span>{refusal.says}</span>
                </div>
              ))}
            </li>
          ))}
        </ol>

        {pipeline && (
          <div className="stage-aside">
            <div className="stage-step-name">And where a write stops</div>
            <p className="stage-step-what">{pipeline.write_path.what}</p>
            <div className="stage-tools">
              {pipeline.write_path.tools.map((name) => (
                <span className="meta-chip" key={name}>{name}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* --- 4. content --- */}

      {viewing && (
        <Preview
          title={`${viewing.schema}.${viewing.relation}`}
          subtitle={
            page
              ? `${page.kind} · ${count(page.total, "row")} · ordered by ${page.ordered_by.join(", ")}`
              : "Reading…"
          }
          onClose={() => setViewing(null)}
        >
          {page && (
            <>
              <div className="table-wrap">
                <table className="rows-table">
                  <thead>
                    <tr>
                      {page.columns.map((column) => (
                        <th key={column.name} title={column.type}>{column.name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {page.rows.map((row, i) => (
                      <tr key={i}>
                        {page.columns.map((column) => (
                          // The full value as a title: a cell is clipped with an ellipsis
                          // when it is too long for the column, and a reader who cannot
                          // recover what was clipped has been shown a half-fact.
                          <td key={column.name} title={cell(row[column.name])}>
                            {cell(row[column.name])}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {!page.rows.length && (
                      <tr>
                        <td colSpan={page.columns.length} className="muted-cell">
                          No rows here.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="rows-foot">
                {/* The cap is stated whether or not it bites. A viewer that returns the
                    first fifty of five hundred without saying so has told the reader
                    something false about the size of the table. The API says the same
                    thing in `cap_note` for anything reading it without a browser; here
                    the window is spelled out instead, because the reader can see it. */}
                <span className="rows-note">
                  {page.total === 0 ? (
                    <>This relation is empty — no rows at all, rather than none shown.</>
                  ) : (
                    <>
                      Rows {page.offset + 1}–{page.offset + page.returned} of{" "}
                      {page.total.toLocaleString()} — at most {page.cap} per request, and
                      nothing between them is dropped.
                    </>
                  )}
                </span>
                <span className="rows-pager">
                  <button
                    className="btn btn--quiet"
                    disabled={paging || page.offset === 0}
                    onClick={() =>
                      setViewing({ ...viewing, offset: Math.max(0, page.offset - PAGE) })
                    }
                  >
                    Previous
                  </button>
                  <button
                    className="btn btn--quiet"
                    disabled={paging || page.offset + page.returned >= page.total}
                    onClick={() => setViewing({ ...viewing, offset: page.offset + PAGE })}
                  >
                    Next
                  </button>
                </span>
              </div>
            </>
          )}
        </Preview>
      )}

      {openTool && (
        <Preview
          title={openTool.name}
          subtitle={`${openTool.write ? "write" : "read"} · tier ${openTool.tier} · ${
            openTool.min_role
          } and above${openTool.scope ? ` · ${openTool.scope}` : ""}`}
          onClose={() => setOpenTool(null)}
        >
          <p className="doc-text">{openTool.purpose}</p>

          <div className="section-title">
            {openTool.write ? "What it takes" : "What it returns"}
          </div>
          <dl className="fact-list">
            {Object.entries(openTool.reads).map(([field, meaning]) => (
              <div className="fact" key={field}>
                <dt>{field}</dt>
                <dd>{meaning}</dd>
              </div>
            ))}
          </dl>

          <div className="section-title">When it is called</div>
          <p className="doc-text">{openTool.asked_as.how}</p>
          {openTool.asked_as.phrases.length > 0 && (
            <>
              <p className="spaces-note">Phrases that point at this tool specifically:</p>
              <div className="stage-tools">
                {openTool.asked_as.phrases.map((phrase) => (
                  <span className="meta-chip" key={phrase}>{phrase}</span>
                ))}
              </div>
            </>
          )}
          {Object.entries(openTool.asked_as.subject_words).map(([subject, words]) => (
            <div key={subject}>
              <p className="spaces-note">
                Words that put it in the running, as a question about {subject}:
              </p>
              <div className="stage-tools">
                {words.map((word) => (
                  <span className="meta-chip" key={word}>{word}</span>
                ))}
              </div>
            </div>
          ))}
          {!openTool.subjects.length && !openTool.asked_as.phrases.length && (
            <p className="spaces-note">
              Nothing in the catalogue points at this tool yet, so the gate can never choose
              it. That is drift, not a design: it needs a subject.
            </p>
          )}
        </Preview>
      )}
    </div>
  );
}

/** One value in a row. Nulls are shown as null rather than as an empty cell, because an
 *  empty cell and a missing value read identically and only one of them is true. */
function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function SpaceBlock({ space, onOpen }: { space: Space; onOpen: (relation: Relation) => void }) {
  return (
    <section className="space">
      <div className="space-head">
        <h3 className="space-name">{space.schema}</h3>
        <div className="space-meta">
          {count(space.relation_count, "relation")} · {count(space.row_total, "row")}
        </div>
      </div>
      <p className="space-purpose">
        {space.purpose ?? (
          <em>
            No COMMENT ON SCHEMA recorded — the database does not say what this space is
            for.
          </em>
        )}
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>relation</th><th>kind</th><th>rows</th><th>who can read it</th></tr>
          </thead>
          <tbody>
            {space.relations.map((relation) => (
              <tr key={relation.name}>
                <td>
                  {relation.browsable ? (
                    <button className="linkish" onClick={() => onOpen(relation)}>
                      {relation.name}
                    </button>
                  ) : (
                    <span className="mono-cell">{relation.name}</span>
                  )}
                </td>
                <td className="muted-cell">{relation.kind}</td>
                <td>{relation.rows?.toLocaleString() ?? "—"}</td>
                <td className="muted-cell">
                  {grantSummary(relation)}
                  {relation.not_browsable && (
                    <div className="rel-note">{relation.not_browsable}</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
