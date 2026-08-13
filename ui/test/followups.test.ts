import { test } from "node:test";
import assert from "node:assert/strict";
import { followUpsFor } from "../src/followups.ts";
import type { AgentResponse } from "../src/types.ts";
import { CAPTURED } from "./payloads.ts";

const texts = (response: AgentResponse) => followUpsFor(response).map((f) => f.text);

/* --- the property that matters most ------------------------------------------------ */

test("no suggested next step quotes a value the response did not contain", () => {
  /* This is the whole safety case for the feature. A chip is a sentence the UI wrote, and
     the moment it can put a period, an account code or an instrument name into that
     sentence, it can put in one that was never in the payload — which is the UI becoming
     a second source of truth. Each chip carries the values it quoted, and every one of
     them has to be findable in the payload it was derived from. */
  for (const [name, captured] of Object.entries(CAPTURED)) {
    const serialised = JSON.stringify(captured.payload);
    for (const chip of followUpsFor(captured.payload)) {
      for (const value of chip.values) {
        assert.ok(
          serialised.includes(value),
          `chip "${chip.text}" on ${name} quotes ${JSON.stringify(value)}, ` +
            "which is not anywhere in the payload",
        );
      }
    }
  }
});

test("every suggested next step is a sentence a person could have typed", () => {
  /* A chip sends its own text as a message. Anything with a newline in it, or long enough
     to be a paragraph, is a sign a payload value reached the sentence unchecked. */
  for (const captured of Object.values(CAPTURED)) {
    for (const chip of followUpsFor(captured.payload)) {
      assert.ok(chip.text.trim().length > 0);
      assert.ok(!/[\n\r]/.test(chip.text), `newline in chip: ${JSON.stringify(chip.text)}`);
      assert.ok(chip.text.length <= 120, `chip too long: ${chip.text}`);
    }
  }
});

test("no turn is offered more than four next steps", () => {
  /* Past four a row of choices is a menu to read, which costs more attention than it
     saves. */
  for (const captured of Object.values(CAPTURED)) {
    assert.ok(followUpsFor(captured.payload).length <= 4);
  }
});

/* --- what fires, on real payloads -------------------------------------------------- */

test("an invoice answer offers the statement for that account and period", () => {
  /* The two values the printable statement needs are the two the billing tool reported,
     so the offer can be made without asking the reader for either. */
  assert.deepEqual(texts(CAPTURED.invoice.payload), [
    "Generate an invoice statement for ACC-A1 for 2026-03",
  ]);
});

test("an availability answer offers to book the free slot it just showed", () => {
  /* The rows of an availability lookup are the free windows themselves, so the slot in
     the offer is a slot the server returned rather than one the UI composed. */
  assert.deepEqual(texts(CAPTURED.availability.payload), [
    "Book Confocal C2 on 2026-03-18 from 08:00 to 20:00",
  ]);
});

test("a list of twenty bookings does not offer a document about one of them", () => {
  /* Twenty rows hold twenty booking ids and no reason to prefer any of them; picking the
     first would be the UI choosing a fact on the reader's behalf. The rules behind the
     list are still worth offering, because that offer names no booking at all. */
  assert.deepEqual(texts(CAPTURED.bookings.payload), ["What does the cancellation policy say?"]);
});

test("a single booking does offer the confirmation for it", () => {
  const one: AgentResponse = {
    ...CAPTURED.bookings.payload,
    rows: [CAPTURED.bookings.payload.rows[0]],
  };
  assert.deepEqual(texts(one), [
    "Generate a booking confirmation for bk-0133",
    "What does the cancellation policy say?",
  ]);
});

test("a facilities card naming one core offers to look inside that core", () => {
  assert.deepEqual(texts(CAPTURED.facilities.payload), [
    "What instruments does the Advanced Imaging Core have?",
    "Generate a facility directory",
  ]);
});

test("an instruments card offers the capability report for the goal the tool understood", () => {
  /* Quoted, because the goal in the report is the tool's normalisation of the question
     ("image"), not the sentence the reader typed. */
  assert.deepEqual(texts(CAPTURED.instruments.payload), [
    'Generate a capability report for "image"',
  ]);
});

/* --- and, more importantly, what does not ------------------------------------------ */

test("a reply that is not an answer from records offers nothing", () => {
  /* Being asked "what next?" by a reply that just declined to answer, asked for a
     clarification, or said hello reads as the product not listening. */
  for (const name of ["policy", "smalltalk", "outOfScope", "refusal"]) {
    assert.deepEqual(followUpsFor(CAPTURED[name].payload), [], `${name} should offer nothing`);
  }
});

test("a pending action offers nothing beside its approval card", () => {
  /* The decision is the next step. A chip beside it competes with the one thing the turn
     is asking for. */
  assert.deepEqual(followUpsFor(CAPTURED.documentProposal.payload), []);
});

test("an answer from arbitrary SQL offers nothing", () => {
  /* A SELECT the planner wrote tells us which views were read and nothing about what
     would help next, so there is nothing here to derive an offer from. */
  assert.deepEqual(followUpsFor(CAPTURED.downtimeSql.payload), []);
});

test("a missing or malformed value suppresses the chip that would have quoted it", () => {
  /* The failure this prevents is the loud one: an invoice chip promising a statement for
     a period the payload never carried. A rule that cannot fill its sentence from the
     payload does not fill it at all. */
  const base = CAPTURED.invoice.payload;
  const withoutPeriod: AgentResponse = {
    ...base,
    meta: { ...base.meta, result_facts: { account_code: "ACC-A1" } },
  };
  assert.deepEqual(followUpsFor(withoutPeriod), []);

  const nonsensePeriod: AgentResponse = {
    ...base,
    meta: { ...base.meta, result_facts: { account_code: "ACC-A1", period: "last March" } },
  };
  assert.deepEqual(followUpsFor(nonsensePeriod), []);

  const numericPeriod: AgentResponse = {
    ...base,
    meta: { ...base.meta, result_facts: { account_code: "ACC-A1", period: 202603 } },
  };
  assert.deepEqual(followUpsFor(numericPeriod), []);
});

test("a slot spanning two days is not offered as a booking", () => {
  /* "Book X on <date> from <a> to <b>" can only be said of a window inside one day.
     Given an overnight slot the sentence would be wrong, so it is not said. */
  const base = CAPTURED.availability.payload;
  const overnight: AgentResponse = {
    ...base,
    rows: [{ starts_at: "2026-03-18T18:00:00+00:00", ends_at: "2026-03-19T02:00:00+00:00" }],
  };
  assert.deepEqual(followUpsFor(overnight), []);
});

test("meta that is not shaped like a plan is ignored rather than trusted", () => {
  /* meta is an open bag on the wire. Everything read out of it is checked for shape
     first, because a rule that assumes will throw inside a render and take the whole
     reply off the screen. */
  const base = CAPTURED.invoice.payload;
  for (const meta of [{}, { plan: null }, { plan: "get_billing_summary" }, { plan: [] }]) {
    assert.deepEqual(followUpsFor({ ...base, meta } as AgentResponse), []);
  }
  assert.deepEqual(
    followUpsFor({ ...base, meta: { plan: { tool: "get_billing_summary" } } } as AgentResponse),
    [],
  );
});
