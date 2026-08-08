import { useEffect, useState } from "react";
import { adminAudit, adminEvals, adminSummary, adminTraces } from "../api";

type Row = Record<string, unknown>;

export function Admin() {
  const [summary, setSummary] = useState<Row | null>(null);
  const [audit, setAudit] = useState<{
    actions: Row[];
    events: Row[];
    kinds: string[];
    statuses: string[];
  } | null>(null);
  const [traces, setTraces] = useState<{ sink: string; langfuse_url: string | null; spans: Row[] } | null>(null);
  const [evals, setEvals] = useState<Row | null>(null);
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const [s, a, t, e] = await Promise.all([
        adminSummary(),
        adminAudit({ status: status || undefined, kind: kind || undefined }),
        adminTraces(),
        adminEvals(),
      ]);
      setSummary(s);
      setAudit(a);
      setTraces(t);
      setEvals(e.latest);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load admin data.");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, kind]);

  const metric = (key: string) =>
    evals && evals[key] !== null && evals[key] !== undefined ? String(evals[key]) : "n/a";

  return (
    <div className="admin">
      <div className="admin-inner">
        {error && <div className="error-banner">{error}</div>}

        <div className="cards">
          <div className="card">
            <div className="label">Model</div>
            <div className="value" style={{ fontSize: 15 }}>{String(summary?.model ?? "—")}</div>
            <div className="sub">reranker {String(summary?.reranker ?? "—")}</div>
          </div>
          <div className="card">
            <div className="label">Knowledge docs</div>
            <div className="value">{String(summary?.knowledge_docs ?? "—")}</div>
            <div className="sub">permission-filtered at query time</div>
          </div>
          <div className="card">
            <div className="label">Escalation</div>
            <div className="value" style={{ fontSize: 15 }}>
              {summary?.escalation_enabled ? "enabled" : "disabled"}
            </div>
            <div className="sub">no cloud calls in the core path</div>
          </div>
          <div className="card">
            <div className="label">Traces</div>
            <div className="value" style={{ fontSize: 15 }}>{traces?.sink ?? "—"}</div>
            <div className="sub">
              {traces?.langfuse_url ? (
                <a href={traces.langfuse_url} target="_blank" rel="noreferrer">
                  open Langfuse
                </a>
              ) : (
                "logs/traces.jsonl"
              )}
            </div>
          </div>
        </div>

        <div className="section-title">Latest eval run</div>
        {evals ? (
          <div className="cards">
            <div className="card">
              <div className="label">Faithfulness</div>
              <div className="value">{metric("faithfulness")}</div>
              <div className="sub">target 0.90 · report-only</div>
            </div>
            <div className="card">
              <div className="label">Answer correctness</div>
              <div className="value">{metric("answer_correctness")}</div>
              <div className="sub">target 0.85 · report-only</div>
            </div>
            <div className="card">
              <div className="label">Data exact-match</div>
              <div className="value">{metric("data_exact_match")}</div>
              <div className="sub">must be 1.0 · enforced</div>
            </div>
            <div className="card">
              <div className="label">Redirect / forbidden</div>
              <div className="value">{metric("redirect_forbidden")}</div>
              <div className="sub">must be 1.0 · enforced</div>
            </div>
          </div>
        ) : (
          <p style={{ color: "#5b6470", fontSize: 13 }}>No eval run yet — run <code>make eval</code>.</p>
        )}

        <div className="section-title">Audit trail</div>
        <div className="filters">
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">all statuses</option>
            {(audit?.statuses ?? []).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">all kinds</option>
            {(audit?.kinds ?? []).map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <button onClick={() => void load()}>Refresh</button>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>action</th><th>user</th><th>tool</th><th>status</th>
                <th>approver</th><th>created</th><th>executed</th>
              </tr>
            </thead>
            <tbody>
              {(audit?.actions ?? []).map((a) => (
                <tr key={String(a.id)}>
                  <td style={{ fontFamily: "var(--mono)", fontSize: 11.5 }}>{String(a.id)}</td>
                  <td>{String(a.user_id)}</td>
                  <td>{String(a.tool)}</td>
                  <td><span className={`pill ${String(a.status)}`}>{String(a.status)}</span></td>
                  <td>{String(a.approver_id ?? "—")}</td>
                  <td>{String(a.created_at ?? "").slice(0, 19).replace("T", " ")}</td>
                  <td>{String(a.executed_at ?? "—").slice(0, 19).replace("T", " ")}</td>
                </tr>
              ))}
              {!audit?.actions.length && (
                <tr><td colSpan={7} style={{ color: "#8b94a0" }}>No actions recorded.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="section-title">Recent events</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>when</th><th>event</th><th>actor</th><th>tool</th><th>action</th></tr>
            </thead>
            <tbody>
              {(audit?.events ?? []).slice(0, 25).map((e, i) => (
                <tr key={i}>
                  <td>{String(e.created_at ?? "").slice(0, 19).replace("T", " ")}</td>
                  <td><span className={`pill ${String(e.event)}`}>{String(e.event)}</span></td>
                  <td>{String(e.actor_id)}</td>
                  <td>{String(e.tool ?? "—")}</td>
                  <td style={{ fontFamily: "var(--mono)", fontSize: 11.5 }}>{String(e.action_id ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="section-title">Latest traces ({traces?.sink})</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>when</th><th>span</th><th>route</th><th>gate</th><th>sql</th><th>ms</th></tr>
            </thead>
            <tbody>
              {(traces?.spans ?? []).slice(0, 25).map((s, i) => (
                <tr key={i}>
                  <td>{String(s.ts ?? "")}</td>
                  <td>{String(s.name)}</td>
                  <td>{String(s.route ?? "—")}</td>
                  <td>{String(s.gate_result ?? "—")}</td>
                  <td>{String(s.sql_valid ?? "—")}</td>
                  <td>{String(s.duration_ms ?? "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
