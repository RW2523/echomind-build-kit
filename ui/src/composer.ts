/**
 * Every keyboard decision the composer makes, and the transcript's stick-to-bottom rule,
 * as functions with no React and no DOM in them.
 *
 * They live here because the bug this kind of code produces is invisible from the
 * server: a handler that reads the wrong modifier, or an Escape that cancels a turn when
 * the reader only meant to close a preview, breaks nothing that a request/response test
 * can see. Pulled out, each decision is a table of inputs and one answer.
 */

export interface KeyLike {
  key: string;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  /** True mid-IME composition, where Enter commits a candidate and means nothing else. */
  isComposing: boolean;
}

export interface ComposerState {
  draft: string;
  /** True once the draft is a message recalled from history rather than typed. */
  recalling: boolean;
}

export type ComposerIntent = "send" | "newline" | "older" | "newer" | "none";

/**
 * What a keypress in the composer means.
 *
 * Enter sends and Shift+Enter breaks the line, as before. Up walks back through what
 * this person has already asked — but only from an empty box, or from a draft that is
 * still exactly the recalled message. The moment they edit it, Up is a caret key again;
 * a history walk that eats an edited draft loses work the reader cannot get back.
 */
export function composerIntent(event: KeyLike, state: ComposerState): ComposerIntent {
  if (event.isComposing) return "none";
  if (event.key === "Enter") {
    if (event.ctrlKey || event.metaKey || event.altKey) return "none";
    return event.shiftKey ? "newline" : "send";
  }
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    // A modified arrow belongs to the text field (word jump, selection, OS shortcut).
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return "none";
    const inHistory = state.draft === "" || state.recalling;
    if (!inHistory) return "none";
    if (event.key === "ArrowUp") return "older";
    // Down out of an empty box would walk forward from nowhere.
    return state.recalling ? "newer" : "none";
  }
  return "none";
}

export interface Recall {
  /** Position in the history list, or null for "back at the draft I was writing". */
  cursor: number | null;
  draft: string;
}

/**
 * One step through the questions already asked, oldest first.
 *
 * Walking past the newest entry returns to an empty box rather than sticking on the last
 * message: a reader who has arrowed too far needs a way back to typing that is not
 * select-all-delete. Walking past the oldest stays put.
 */
export function recall(
  history: readonly string[],
  cursor: number | null,
  direction: "older" | "newer",
): Recall {
  if (!history.length) return { cursor: null, draft: "" };
  if (direction === "older") {
    const next = cursor === null ? history.length - 1 : Math.max(0, cursor - 1);
    return { cursor: next, draft: history[next] };
  }
  if (cursor === null || cursor >= history.length - 1) return { cursor: null, draft: "" };
  const next = cursor + 1;
  return { cursor: next, draft: history[next] };
}

export interface EscapeContext {
  sending: boolean;
  /** True while a preview is open over the conversation. */
  dialogOpen: boolean;
}

/**
 * Whether Escape should stop the turn in flight.
 *
 * The preview listens for Escape too, and it is on top: a reader with a source open who
 * presses Escape is closing that source, not abandoning the answer underneath it. Both
 * listeners see the same event, so the one further from the reader's attention has to
 * stand down.
 */
export function shouldStopStream(event: KeyLike, context: EscapeContext): boolean {
  if (event.key !== "Escape" || event.isComposing) return false;
  return context.sending && !context.dialogOpen;
}

/** How close to the bottom still counts as "reading the newest thing". Roughly one line
 *  of prose plus the fade over the composer, so a reader who has nudged the wheel is not
 *  treated as having left. */
export const STICK_THRESHOLD_PX = 140;

/**
 * Whether the transcript should keep following the answer as it streams.
 *
 * False as soon as the reader scrolls up: a transcript that yanks itself back down while
 * someone is reading an earlier answer is unusable, which is why this rule already
 * existed inline. It is here so the threshold can be tested rather than trusted.
 */
export function shouldStickToBottom(view: {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}): boolean {
  return view.scrollHeight - view.scrollTop - view.clientHeight < STICK_THRESHOLD_PX;
}
