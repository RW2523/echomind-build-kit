/* First, and deliberately: this installs the DOM that react-dom inspects as it loads.
   Any import above it would leave React convinced it is running without a browser. */
import { win } from "./support/dom-env.ts";

import { after, test } from "node:test";
import assert from "node:assert/strict";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import App from "../src/App.tsx";
import { CAPTURED } from "./payloads.ts";

/**
 * The wiring between a keypress and the decision it triggers, exercised rather than read.
 *
 * The unit tests prove what `composerIntent` answers for a given key, and the render smoke
 * test proves the components draw. Neither can see the seam between them: a handler bound
 * to the wrong event, an `onSend` that never reaches the chip, a Stop button that renders
 * but aborts nothing, a focus hand-off that was written and never runs. That seam is
 * precisely where the bug this repo keeps naming lives — something built, imported, and
 * never actually connected — and until this file existed it was covered by review only.
 *
 * So: the real App, mounted in a real DOM, driven by real events, over the real api.ts.
 * Only the network is a stand-in, and it is stubbed at `fetch` rather than at the module
 * boundary on purpose — that keeps streamChat's own SSE parsing and abort handling inside
 * what is under test instead of replacing it with a fake that always behaves.
 */

/* ------------------------------------------------------------------ the fake network */

interface ChatCall {
  message: string;
  thread_id: string | null;
  authorization: string | null;
}

/** Every /chat/stream request the UI made, in order, with the body it actually sent. */
let chatCalls: ChatCall[] = [];
/** The stream of the turn in flight, so a test can deliver events one at a time. */
let live: ReadableStreamDefaultController<Uint8Array> | null = null;

/* Shaped from the live responses, including the part that is easy to get wrong: the login
   endpoint returns the handle beside the user, not inside it, so `user` here has none.
   Inventing one would quietly retire the fold-in that loginAs does to keep the rail, the
   openers and the saved thread from all keying on `undefined`. */
const ALICE = {
  id: "u-alice",
  name: "Alice Nguyen",
  role: "user",
  lab_ids: ["lab-a"],
  blurb: "Researcher, Lab A",
};

const encoder = new TextEncoder();

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function frame(event: string, data: unknown) {
  if (!live) throw new Error("no turn is streaming");
  live.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
}

/* Stubbed at `fetch` rather than at the api module so that api.ts — its SSE framing, its
   bearer header, its abort handling — stays inside what these tests exercise. Node's own
   Response and ReadableStream do the work, so the streaming path is the real one. */
(globalThis as Record<string, unknown>).fetch = async (
  input: unknown,
  init?: RequestInit,
): Promise<Response> => {
  const url = String(input);
  if (url === "/demo/users") return jsonResponse({ users: [{ ...ALICE, handle: "alice" }] });
  if (url.startsWith("/demo/login/")) {
    return jsonResponse({ token: "demo-token", handle: "alice", user: ALICE });
  }
  if (url === "/uploads") return jsonResponse({ uploads: [] });
  if (url === "/chat/stream") {
    const body = JSON.parse(String(init?.body ?? "{}"));
    const auth = new Headers(init?.headers as HeadersInit).get("Authorization");
    chatCalls.push({ message: body.message, thread_id: body.thread_id, authorization: auth });
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        live = controller;
      },
    });
    /* Real fetch errors the body when the caller aborts; without this the UI would abort
       into a stream that keeps politely waiting, and the Stop path would look like it
       worked while proving nothing. */
    init?.signal?.addEventListener("abort", () => {
      try {
        live?.error(new DOMException("The operation was aborted.", "AbortError"));
      } catch {
        /* already closed */
      }
      live = null;
    });
    return new Response(stream, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }
  throw new Error(`the test network was asked for ${url}, which it does not serve`);
};

/* ------------------------------------------------------------------------- the driver */

interface Mounted {
  container: HTMLElement;
  unmount: () => Promise<void>;
}

async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function mount(): Promise<Mounted> {
  chatCalls = [];
  live = null;
  win.localStorage.clear();
  const container = win.document.createElement("div");
  win.document.body.appendChild(container);
  let root: Root | null = null;
  await act(async () => {
    root = createRoot(container);
    root.render(<App />);
  });
  // The shell signs in, restores a thread and lists uploads before it is usable.
  await settle();
  await settle();
  return {
    container,
    unmount: async () => {
      await act(async () => root?.unmount());
      container.remove();
    },
  };
}

/** React tracks the value it last wrote, so assigning `.value` directly is ignored as a
 *  no-op change. Going through the prototype setter is what a real keystroke looks like. */
function typeInto(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLTextAreaElement.prototype, "value")!.set!;
  setter.call(el, value);
  el.dispatchEvent(new win.Event("input", { bubbles: true }));
}

async function press(el: EventTarget, key: string, init: KeyboardEventInit = {}) {
  await act(async () => {
    el.dispatchEvent(
      new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init }),
    );
  });
}

async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

/**
 * Identity, without letting assert stringify a DOM node.
 *
 * `assert.equal(activeElement, button)` on two jsdom elements builds a diff of two
 * enormous object graphs when it fails, which takes long enough to look like the suite
 * has hung rather than failed — found while mutation-testing this file, where a genuinely
 * caught regression presented as a timeout.
 */
function assertSame(actual: unknown, expected: unknown, message: string) {
  assert.ok(actual === expected, message);
}

/** What has focus, named so a failure says something a person can act on. */
function focused(): string {
  const el = win.document.activeElement as HTMLElement | null;
  if (!el || el === win.document.body) return "<body>";
  const label = el.getAttribute("aria-label") ?? el.className ?? "";
  return `${el.tagName.toLowerCase()}${label ? ` [${label}]` : ""}`;
}

const composer = (c: HTMLElement) => c.querySelector("textarea") as HTMLTextAreaElement;
const chipTexts = (c: HTMLElement) =>
  [...c.querySelectorAll(".follow-up")].map((b) => b.textContent ?? "");

/** Plays a complete turn: the stages, the prose, then the verified payload. */
async function deliver(payload: unknown) {
  await act(async () => {
    frame("status", { stage: "checking access" });
    frame("token", { text: "Your March invoice " });
    frame("final", payload);
    live?.close();
    live = null;
  });
  await settle();
}

/* jsdom's visual mode keeps an animation-frame timer running, which holds the event loop
   open after the last assertion: the suite passes and the command never returns, which in
   CI is indistinguishable from a hang. Closing the window releases it. */
after(() => win.close());

/* -------------------------------------------------------------------------- the tests */

test("typing a question and pressing Enter sends it and renders the verified answer", async () => {
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");

    assert.equal(chatCalls.length, 1, "Enter did not reach the send path");
    assert.equal(chatCalls[0].message, "What is on my March invoice?");
    assert.equal(chatCalls[0].authorization, "Bearer demo-token", "the turn went out unauthenticated");

    await deliver(CAPTURED.invoice.payload);
    const text = app.container.textContent ?? "";
    assert.ok(text.includes("inv-ACC-A1-2026-03"), "the answer never reached the screen");
    assert.ok(text.includes("From the records"), "a rows answer lost its badge");
    assert.equal(composer(app.container).value, "", "the draft survived being sent");
  } finally {
    await app.unmount();
  }
});

test("a suggested next step sends its own words down the same path a typed question takes", async () => {
  /* The whole safety case for the chips in one assertion: the second request is an
     ordinary /chat/stream carrying the chip's visible text. No tool is named, no action id
     is passed, nothing is executed — so the router, the permission checks and the approval
     card all still stand between the click and any write. */
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");
    await deliver(CAPTURED.invoice.payload);

    const chips = [...app.container.querySelectorAll(".follow-up")];
    assert.equal(chips.length, 1, `expected one chip, got ${JSON.stringify(chipTexts(app.container))}`);
    const label = chips[0].textContent ?? "";
    assert.equal(label, "Generate an invoice statement for ACC-A1 for 2026-03");

    await click(chips[0]);
    assert.equal(chatCalls.length, 2, "the chip is a button that does nothing");
    assert.equal(chatCalls[1].message, label, "the chip asked something other than what it said");
  } finally {
    await app.unmount();
  }
});

test("Shift+Enter breaks the line instead of sending", async () => {
  const app = await mount();
  try {
    typeInto(composer(app.container), "two lines");
    await press(composer(app.container), "Enter", { shiftKey: true });
    assert.equal(chatCalls.length, 0, "Shift+Enter sent the message");
  } finally {
    await app.unmount();
  }
});

test("Up-arrow on an empty composer brings back the last question to edit", async () => {
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");
    await deliver(CAPTURED.invoice.payload);
    assert.equal(composer(app.container).value, "");

    await press(composer(app.container), "ArrowUp");
    assert.equal(
      composer(app.container).value,
      "What is on my March invoice?",
      "Up did not recall the question",
    );

    // Down past the newest returns to an empty box rather than sticking on the last message.
    await press(composer(app.container), "ArrowDown");
    assert.equal(composer(app.container).value, "");
  } finally {
    await app.unmount();
  }
});

test("Up-arrow leaves an edited draft alone rather than eating it", async () => {
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");
    await deliver(CAPTURED.invoice.payload);

    typeInto(composer(app.container), "a draft I am still writing");
    await press(composer(app.container), "ArrowUp");
    assert.equal(
      composer(app.container).value,
      "a draft I am still writing",
      "the history walk swallowed work the reader cannot get back",
    );
  } finally {
    await app.unmount();
  }
});

test("while a turn is in flight the Stop button holds focus the composer had to give up", async () => {
  /* Disabling a focused element blurs it. Without the hand-off a keyboard reader is left
     at the top of the document while the only control that matters is further down. */
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");

    const stop = app.container.querySelector(".send-btn--stop");
    assert.ok(stop, "no Stop control appeared while the turn was running");
    assert.equal(composer(app.container).disabled, true);
    assertSame(
      win.document.activeElement,
      stop,
      `focus was dropped when the composer was disabled — it is on ${focused()}`,
    );
  } finally {
    live?.close();
    await app.unmount();
  }
});

test("Escape stops the turn in flight and says so without claiming a cancellation", async () => {
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");
    await act(async () => {
      frame("status", { stage: "checking access" });
    });

    await press(win, "Escape");
    await settle();

    const text = app.container.textContent ?? "";
    assert.ok(text.includes("Stopped"), "Escape did not stop the turn");
    assert.ok(
      text.includes("may already have finished on the server"),
      "the UI claimed a cancellation the API cannot perform",
    );
    assert.equal(composer(app.container).disabled, false, "the composer stayed locked after stopping");
  } finally {
    await app.unmount();
  }
});

test("Escape belongs to an open preview, not to the turn behind it", async () => {
  /* Both listeners see the same key. A reader with a source open is closing that source;
     stopping the answer underneath it would be the UI acting on the wrong intent. */
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");
    await act(async () => {
      frame("status", { stage: "checking access" });
    });

    const dialog = win.document.createElement("div");
    dialog.setAttribute("role", "dialog");
    win.document.body.appendChild(dialog);
    await press(win, "Escape");
    await settle();

    assert.ok(
      !(app.container.textContent ?? "").includes("Stopped"),
      "Escape stopped the turn while a preview was open over it",
    );
    dialog.remove();
  } finally {
    live?.close();
    await app.unmount();
  }
});

test("the rows behind an answer open from the keyboard and hand focus back on close", async () => {
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");
    await deliver(CAPTURED.invoice.payload);

    const source = app.container.querySelector(".source-btn") as HTMLButtonElement;
    assert.ok(source, "a rows answer offered no way to see its rows");
    source.focus();
    await click(source);

    const dialog = win.document.querySelector('[role="dialog"]');
    assert.ok(dialog, "the Source control opened nothing");
    assert.ok(
      (dialog.textContent ?? "").includes("Nanopore PromethION"),
      "the evidence table is empty",
    );
    const close = dialog.querySelector(".preview-close") as HTMLButtonElement;
    assertSame(win.document.activeElement, close, `focus never entered the dialog — it is on ${focused()}`);

    await click(close);
    await settle();
    assertSame(win.document.querySelector('[role="dialog"]'), null, "the preview would not close");
    assertSame(
      win.document.activeElement,
      source,
      `focus was not returned to what opened it — it is on ${focused()}`,
    );
  } finally {
    await app.unmount();
  }
});

test("a chip cannot swallow a click while another turn is already running", async () => {
  /* send() refuses a second turn while one is in flight. A chip left enabled through that
     window is a control that accepts the click and does nothing — the reader has no way to
     tell it from a broken one, so it is shown as unavailable instead. */
  const app = await mount();
  try {
    typeInto(composer(app.container), "What is on my March invoice?");
    await press(composer(app.container), "Enter");
    await deliver(CAPTURED.invoice.payload);

    // A second turn, left streaming.
    typeInto(composer(app.container), "and February?");
    await press(composer(app.container), "Enter");
    assert.equal(chatCalls.length, 2);

    const chip = app.container.querySelector(".follow-up") as HTMLButtonElement;
    assert.ok(chip, "the finished reply lost its chips while the next turn ran");
    assert.equal(chip.disabled, true, "the chip stayed clickable while a turn was in flight");

    await click(chip);
    assert.equal(chatCalls.length, 2, "a disabled chip still started a turn");
  } finally {
    live?.close();
    await app.unmount();
  }
});

test("an answer with nothing to suggest renders no chip row at all", async () => {
  const app = await mount();
  try {
    typeInto(composer(app.container), "hello");
    await press(composer(app.container), "Enter");
    await deliver(CAPTURED.smalltalk.payload);
    assert.deepEqual(chipTexts(app.container), []);
  } finally {
    await app.unmount();
  }
});
