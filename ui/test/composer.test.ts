import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STICK_THRESHOLD_PX,
  composerIntent,
  recall,
  shouldStickToBottom,
  shouldStopStream,
  type KeyLike,
} from "../src/composer.ts";

const key = (over: Partial<KeyLike>): KeyLike => ({
  key: "a",
  shiftKey: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  isComposing: false,
  ...over,
});

const writing = { draft: "", recalling: false };

test("Enter sends and Shift+Enter breaks the line", () => {
  assert.equal(composerIntent(key({ key: "Enter" }), writing), "send");
  assert.equal(composerIntent(key({ key: "Enter", shiftKey: true }), writing), "newline");
});

test("Enter mid-composition commits a candidate and never sends", () => {
  /* An IME uses Enter to accept the word being typed. Treating that as "send" posts a
     half-written question in the middle of writing it. */
  assert.equal(composerIntent(key({ key: "Enter", isComposing: true }), writing), "none");
  assert.equal(
    composerIntent(key({ key: "ArrowUp", isComposing: true }), writing),
    "none",
  );
});

test("Up recalls only from an empty box or an untouched recall", () => {
  /* Once the reader has edited the recalled message, Up is a caret key again — a history
     walk that swallows an edited draft loses work there is no way back to. */
  assert.equal(composerIntent(key({ key: "ArrowUp" }), writing), "older");
  assert.equal(
    composerIntent(key({ key: "ArrowUp" }), { draft: "Show me my", recalling: false }),
    "none",
  );
  assert.equal(
    composerIntent(key({ key: "ArrowUp" }), { draft: "Show me my bookings", recalling: true }),
    "older",
  );
});

test("Down walks forward only from inside the history", () => {
  assert.equal(composerIntent(key({ key: "ArrowDown" }), writing), "none");
  assert.equal(
    composerIntent(key({ key: "ArrowDown" }), { draft: "anything", recalling: true }),
    "newer",
  );
});

test("a modified arrow belongs to the text field", () => {
  /* Ctrl/Alt/Cmd/Shift + arrow are word jumps, selections and OS shortcuts. Stealing them
     for history breaks editing in a box people write paragraphs in. */
  for (const modifier of ["shiftKey", "altKey", "ctrlKey", "metaKey"] as const) {
    assert.equal(composerIntent(key({ key: "ArrowUp", [modifier]: true }), writing), "none");
  }
});

test("walking the history stops at the oldest and returns to an empty box past the newest", () => {
  /* A reader who has arrowed too far needs a way back to typing that is not
     select-all-delete. */
  const asked = ["first", "second", "third"];
  const one = recall(asked, null, "older");
  assert.deepEqual(one, { cursor: 2, draft: "third" });
  const two = recall(asked, one.cursor, "older");
  assert.deepEqual(two, { cursor: 1, draft: "second" });
  const three = recall(asked, two.cursor, "older");
  assert.deepEqual(three, { cursor: 0, draft: "first" });
  assert.deepEqual(recall(asked, 0, "older"), { cursor: 0, draft: "first" });

  assert.deepEqual(recall(asked, 0, "newer"), { cursor: 1, draft: "second" });
  assert.deepEqual(recall(asked, 2, "newer"), { cursor: null, draft: "" });
  assert.deepEqual(recall(asked, null, "newer"), { cursor: null, draft: "" });
});

test("an empty history leaves the box alone", () => {
  assert.deepEqual(recall([], null, "older"), { cursor: null, draft: "" });
});

test("Escape stops a turn in flight, and nothing else", () => {
  assert.ok(shouldStopStream(key({ key: "Escape" }), { sending: true, dialogOpen: false }));
  assert.ok(!shouldStopStream(key({ key: "Escape" }), { sending: false, dialogOpen: false }));
  assert.ok(!shouldStopStream(key({ key: "Enter" }), { sending: true, dialogOpen: false }));
});

test("Escape closes an open preview instead of stopping the turn under it", () => {
  /* Both listeners see the same keypress. A reader with a source open is closing that
     source; losing the answer underneath it as well would be an expensive surprise. */
  assert.ok(!shouldStopStream(key({ key: "Escape" }), { sending: true, dialogOpen: true }));
});

test("the transcript follows the tail only while the reader is at it", () => {
  /* A transcript that yanks itself back down while someone is reading an earlier answer
     is unusable, so the rule that decides it is worth pinning to a number. */
  assert.ok(shouldStickToBottom({ scrollHeight: 1000, scrollTop: 900, clientHeight: 100 }));
  assert.ok(
    shouldStickToBottom({
      scrollHeight: 1000,
      scrollTop: 900 - (STICK_THRESHOLD_PX - 1),
      clientHeight: 100,
    }),
  );
  assert.ok(
    !shouldStickToBottom({
      scrollHeight: 1000,
      scrollTop: 900 - STICK_THRESHOLD_PX,
      clientHeight: 100,
    }),
  );
});
