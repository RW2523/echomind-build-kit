/**
 * The live account of what the assistant is doing while it works.
 *
 * Only the stages the server has actually announced are drawn — the list is deliberately
 * not hard-coded here. A fixed checklist would keep showing "verifying against sources"
 * as a step that is coming even on a turn that never reaches it, which is the one claim
 * this product cannot make loosely: the verification shown must be verification that ran.
 */
export function StageTrail({ stages }: { stages: string[] }) {
  const shown = stages.length ? stages : ["working on it"];

  return (
    <div className="stage-trail" role="status" aria-live="polite">
      {shown.map((stage, i) => {
        const done = i < shown.length - 1;
        return (
          <div key={`${i}-${stage}`} className={`stage ${done ? "is-done" : "is-active"}`}>
            <span className="stage-mark" aria-hidden="true" />
            <span className="stage-label">{stage}</span>
          </div>
        );
      })}
    </div>
  );
}
