import { test } from "node:test";
import assert from "node:assert/strict";
import { streamChat } from "../src/api.ts";
import type { AgentResponse } from "../src/types.ts";

/**
 * The stream reader, driven against a fetch that this file controls.
 *
 * /chat/stream has no cancel endpoint, so stopping a turn is an aborted request and
 * nothing more — which makes exactly how that abort is handled the thing worth pinning
 * down. An abort that arrives as an error would put "The connection to the assistant
 * failed" under a turn the reader ended on purpose, and an error that arrives as nothing
 * at all would leave the composer disabled for the rest of the session.
 */

const frame = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/**
 * A fetch whose body emits the given chunks, pausing between them so an abort can land in
 * the middle of the stream rather than only before or after it.
 *
 * The stream errors with an AbortError when the signal fires, because that is what a real
 * fetch does — a stub that merely stopped enqueuing would have this file asserting that
 * an abort is handled while the code under test never saw one.
 */
function fetchOf(chunks: string[], gapMs = 0) {
  return (_input: unknown, init?: { signal?: AbortSignal }) => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      async start(controller) {
        init?.signal?.addEventListener("abort", () => {
          controller.error(new DOMException("The operation was aborted.", "AbortError"));
        });
        for (const chunk of chunks) {
          if (gapMs) await new Promise((r) => setTimeout(r, gapMs));
          if (init?.signal?.aborted) return;
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    });
    return Promise.resolve(new Response(body, { status: 200 }));
  };
}

function withFetch<T>(stub: unknown, run: () => Promise<T>): Promise<T> {
  const original = globalThis.fetch;
  (globalThis as { fetch: unknown }).fetch = stub;
  return run().finally(() => {
    (globalThis as { fetch: unknown }).fetch = original;
  });
}

test("stages, tokens and the final payload reach their own handlers", () => {
  const stages: string[] = [];
  const tokens: string[] = [];
  let final: AgentResponse | null = null;
  const body = [
    frame("start", { thread_id: "thr-abc" }),
    frame("status", { stage: "checking what you may see" }),
    // Two frames arriving in one network chunk, which is the normal case and the one a
    // reader that split on the wrong boundary would drop half of.
    frame("token", { text: "Your March " }) + frame("token", { text: "invoice" }),
    frame("final", { response_type: "rows_answer", text: "Your March invoice", rows: [] }),
  ];
  return withFetch(fetchOf(body), async () => {
    await streamChat("q", null, {
      onStatus: (s) => stages.push(s),
      onToken: (t) => tokens.push(t),
      onFinal: (r) => {
        final = r;
      },
    });
    assert.deepEqual(stages, ["checking what you may see"]);
    assert.deepEqual(tokens, ["Your March ", "invoice"]);
    assert.equal(final?.response_type, "rows_answer");
  });
});

test("a turn the reader stopped is reported as stopped, not as a failure", () => {
  /* Nothing failed. Telling them it did would teach them to distrust a control they used
     correctly. */
  const controller = new AbortController();
  let aborted = false;
  let errored: string | null = null;
  const body = [frame("status", { stage: "one" }), frame("status", { stage: "two" })];
  return withFetch(fetchOf(body, 20), async () => {
    const done = streamChat(
      "q",
      null,
      {
        onStatus: () => controller.abort(),
        onFinal: () => assert.fail("a stopped turn must not deliver a final answer"),
        onError: (m) => {
          errored = m;
        },
        onAborted: () => {
          aborted = true;
        },
      },
      controller.signal,
    );
    await done;
    assert.ok(aborted, "onAborted was never called");
    assert.equal(errored, null);
  });
});

test("a stream that closes with no answer in it says so instead of spinning forever", () => {
  /* A turn left marked "streaming" shows a stage trail that never resolves under a
     composer that has gone back to normal, and the only way out is a reload. A proxy
     timing the connection out is enough to produce it. */
  let errored: string | null = null;
  const body = [frame("status", { stage: "reading the records" })];
  return withFetch(fetchOf(body), async () => {
    await streamChat("q", null, {
      onFinal: () => assert.fail("there was no answer to deliver"),
      onError: (m) => {
        errored = m;
      },
    });
    assert.ok(errored, "a truncated stream produced no message at all");
  });
});

test("a dropped connection is reported rather than thrown at the caller", () => {
  /* It used to escape as a rejected promise, and the `sending` flag it left behind
     disabled the composer until the page was reloaded. */
  let errored: string | null = null;
  const failing = () => Promise.reject(new TypeError("Failed to fetch"));
  return withFetch(failing, async () => {
    await streamChat("q", null, {
      onFinal: () => assert.fail("no answer should arrive"),
      onError: (m) => {
        errored = m;
      },
    });
    assert.ok(errored, "a network failure produced no message at all");
  });
});
