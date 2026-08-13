import { followUpsFor } from "../followups";
import type { AgentResponse } from "../types";

/**
 * The row of next questions under a finished reply.
 *
 * Each one is sent verbatim as a new message, exactly as if it had been typed: the same
 * router, the same permission checks, and the same approval card in front of anything
 * that writes. Nothing here calls a tool, and nothing here can approve.
 *
 * The row disappears entirely when the answer suggests nothing — see followups.ts for
 * why a permanent row of generic chips is worse than none.
 */
export function FollowUps({
  response,
  onSend,
  busy,
}: {
  response: AgentResponse;
  onSend: (message: string) => void;
  /** True while another turn is in flight. */
  busy?: boolean;
}) {
  const suggestions = followUpsFor(response);
  if (!suggestions.length) return null;

  return (
    <div className="follow-ups">
      {/* Labelled by hand rather than by aria-labelledby: a thread holds many of these
          rows, and an id repeated down the page points every one of them at the first. */}
      <span className="follow-ups-label" aria-hidden="true">
        Next
      </span>
      {/* Each chip is disabled while a turn runs because send() refuses a second one:
          left enabled it is a button that takes the click and does nothing, which the
          reader cannot tell from a broken one. Focus is safe to lose here — the shell
          moves it to the Stop button the moment a turn starts. */}
      <div className="follow-ups-row" role="group" aria-label="Suggested next questions">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion.text}
            type="button"
            className="follow-up"
            disabled={busy}
            onClick={() => onSend(suggestion.text)}
          >
            {suggestion.text}
          </button>
        ))}
      </div>
    </div>
  );
}
