import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../src/App.tsx";
import { asCard } from "../src/card.ts";
import { Reply } from "../src/components/Reply.tsx";
import { ResultCard } from "../src/components/ResultCard.tsx";
import { RowsTable } from "../src/components/RowsTable.tsx";
import type { AgentResponse } from "../src/types.ts";
import { CAPTURED } from "./payloads.ts";

/**
 * Does the markup that reaches a browser actually contain the thing that was built?
 *
 * This file exists because of one specific failure: a modal was imported into a component
 * and never rendered, so a button did nothing, and every test in the repo stayed green.
 * A derivation function can be perfect and still never be called. Rendering the real
 * components and reading the real markup is the only check that sees the difference.
 *
 * What it cannot see: effects, clicks and focus — renderToStaticMarkup runs neither, and
 * this repo has no DOM to run them in. So the assertions here are about presence, and
 * anything about behaviour after a click is verified by hand and reported as such rather
 * than implied by a passing test.
 *
 * Run through esbuild (`npm test`), because node's type stripping does not transform JSX.
 */

const markup = (response: AgentResponse) =>
  renderToStaticMarkup(<Reply response={response} onSend={() => undefined} />);

const chips = (html: string) =>
  [...html.matchAll(/class="follow-up">([^<]*)</g)].map((m) => m[1]);

test("the shell renders at all, with a composer and something to ask", () => {
  /* The cheapest check in the file and the one that would have caught the most. Every
     hook in App runs when this renders, so a hook added in the wrong order, a const used
     above where it is declared, or a helper renamed in one place out of two takes this
     down — and every one of those breaks the whole application, not one control in it.
     React prints a useLayoutEffect warning here; that is what rendering a client-only
     component on a server renderer looks like, and it is expected rather than a fault. */
  const globals = globalThis as Record<string, unknown>;
  const store = new Map<string, string>();
  const saved = { window: globals.window, document: globals.document, localStorage: globals.localStorage };
  globals.localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => store.set(k, v),
    removeItem: (k: string) => store.delete(k),
  };
  globals.window = { matchMedia: () => ({ matches: true }) };
  globals.document = { documentElement: {} };
  try {
    const html = renderToStaticMarkup(<App />);
    assert.ok(html.includes("composer"), "no composer in the shell");
    assert.ok(html.includes("send-btn"), "no way to send a question");
    assert.ok(html.includes("Where is the nearest core"), "the empty thread offers nothing");
  } finally {
    Object.assign(globals, saved);
  }
});

test("the suggested next steps reach the markup, not just the function that derives them", () => {
  const html = markup(CAPTURED.invoice.payload);
  assert.deepEqual(chips(html), ["Generate an invoice statement for ACC-A1 for 2026-03"]);
  assert.ok(html.includes('role="group"'), "the chip row has no group for a screen reader");
});

test("a reply with no way to send has no chips to click", () => {
  /* Reply is rendered without onSend in places that only show a turn. Chips drawn there
     would be buttons that do nothing — the exact shape of the bug this file exists for. */
  const html = renderToStaticMarkup(<Reply response={CAPTURED.invoice.payload} />);
  assert.deepEqual(chips(html), []);
});

test("replies that earn no next step render no row at all", () => {
  for (const name of ["smalltalk", "outOfScope", "policy", "documentProposal", "refusal"]) {
    assert.ok(!markup(CAPTURED[name].payload).includes("follow-up"), name);
  }
});

test("rows with evidence behind them still offer the way to check them", () => {
  /* The chips are an addition to the reply, not a replacement for anything: the source
     control has to survive them. */
  const html = markup(CAPTURED.downtimeSql.payload);
  assert.ok(html.includes("source-btn"), "the Source control is gone from a rows answer");
});

test("an absent value is named in the table and a derived column is marked", () => {
  const html = renderToStaticMarkup(
    <RowsTable
      columns={["instrument", "derived_day_rate", "note"]}
      rows={[{ instrument: "Confocal C2", derived_day_rate: "336.00", note: null }]}
    />,
  );
  assert.ok(html.includes("not recorded"));
  assert.ok(html.includes("cell-missing"));
  assert.ok(html.includes(">derived<"), "the platform's derived_ flag did not reach the header");
  assert.ok(html.includes("day rate"), "the derived_ prefix was left in the column name");
  assert.ok(!html.includes("[object Object]"));
});

test("a card field with nothing in it says so rather than rendering an empty line", () => {
  const card = asCard(CAPTURED.facilities.payload.card);
  assert.ok(card, "the captured facilities card no longer parses against the contract");
  assert.ok(renderToStaticMarkup(<ResultCard card={card} />).includes("Advanced Imaging Core"));
  const blank = renderToStaticMarkup(
    <ResultCard card={{ ...card, fields: [{ label: "Campus", value: "  ", emphasis: false }] }} />,
  );
  assert.ok(blank.includes("not recorded"));
});
