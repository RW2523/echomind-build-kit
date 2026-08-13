/**
 * The live account of what the assistant is doing while it works.
 *
 * Only the stages the server has actually announced are drawn — the list is deliberately
 * not hard-coded here. A fixed checklist would keep showing "verifying against sources"
 * as a step that is coming even on a turn that never reaches it, which is the one claim
 * this product cannot make loosely: the verification shown must be verification that ran.
 */
export function StageTrail({ stages, compact }: { stages: string[]; compact?: boolean }) {
  const all = stages.length ? stages : ["working on it"];
  // Once the answer itself starts arriving, the full trail is history and the prose is
  // what the reader is here for — so it collapses to the last thing that happened rather
  // than pushing the reply further down the screen the longer the turn took.
  const shown = compact ? all.slice(-1) : all;

  return (
    <div
      className={`stage-trail${compact ? " stage-trail--compact" : ""}`}
      role="status"
      aria-live="polite"
    >
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
