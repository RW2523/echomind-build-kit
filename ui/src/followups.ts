import type { AgentResponse } from "./types";

/**
 * What a person plausibly wants next, read out of the answer they just got.
 *
 * Every chip here is a question the user could have typed, and clicking one sends it as
 * a normal message on the normal path — same router, same permission checks, same
 * approval card for anything that writes. A chip is a shortcut to the keyboard, never a
 * shortcut past the server.
 *
 * Two rules keep it from becoming a second source of truth:
 *
 *  1. Every value a chip quotes — an account code, a period, an instrument name, a slot
 *     — is copied verbatim out of the payload that is on screen. Nothing is computed,
 *     completed or assumed. `values` carries those strings beside the chip so a test can
 *     assert, for any response, that each one really is in the payload it came from.
 *  2. A rule fires only when the payload holds every part it needs, in a shape that is
 *     recognisably what it claims to be. A missing period means no invoice chip, not an
 *     invoice chip for a guessed month.
 *
 * The catalogue is short on purpose. Each rule was run against the live API and kept
 * only if the question it asks is one the assistant actually answers well; candidates
 * that routed to the wrong tool ("what would cancelling bk-0133 cost me?" came back as
 * a dump of every booking) were dropped rather than shipped hopefully. An always-on row
 * of chips that half work is worse than no row: it teaches the reader to ignore it.
 */
export interface FollowUp {
  /** The message the chip sends, verbatim. The chip shows this same sentence, so what
   *  was clicked and what was asked cannot drift apart. */
  text: string;
  /** The payload values quoted in `text`. Present for the test that proves they were
   *  quoted rather than invented; the renderer ignores it. */
  values: string[];
}

/** Four is the point where a row of choices becomes a menu to read. */
const MAX_FOLLOW_UPS = 4;

/* Shapes, checked before a value is allowed into a sentence. These are not validation
   for the server's benefit — it validates everything again — but a guard against
   putting a value on screen in a role it does not hold: a "period" that is not a month
   would produce a chip promising a document nobody can generate. */
const ACCOUNT_CODE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$/;
const PERIOD = /^\d{4}-\d{2}$/;
const BOOKING_ID = /^bk-[A-Za-z0-9-]{1,16}$/;
/** ISO-8601 as the tools emit it, split into the date and the clock time a person reads. */
const ISO_INSTANT = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}/;
/** A name from the records: one line, and short enough to sit in a sentence. */
const NAME = /^[^\n\r]{1,60}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, shape: RegExp): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return shape.test(trimmed) ? trimmed : null;
}

/** The tool the server says produced this answer, from `meta.plan`. A SQL plan carries
 *  no tool name and reaches none of the rules below, which is correct: an arbitrary
 *  SELECT tells us nothing about what would help next. */
function plannedTool(response: AgentResponse): string | null {
  const plan = response.meta?.plan;
  if (!isRecord(plan)) return null;
  return typeof plan.tool === "string" ? plan.tool : null;
}

/** The tool's own facts about the whole result, from `meta.result_facts`. */
function resultFacts(response: AgentResponse): Record<string, unknown> {
  const facts = response.meta?.result_facts;
  return isRecord(facts) ? facts : {};
}

function firstRow(response: AgentResponse): Record<string, unknown> | null {
  const row = response.rows[0];
  return isRecord(row) ? row : null;
}

/** The date and clock time of an ISO instant, as substrings of it. Slicing a timestamp
 *  into the two halves a person reads is formatting; no arithmetic is done on it, and a
 *  string that is not plainly an instant yields nothing rather than a best effort. */
function instantParts(value: unknown): { date: string; clock: string } | null {
  if (typeof value !== "string") return null;
  const match = ISO_INSTANT.exec(value.trim());
  return match ? { date: match[1], clock: match[2] } : null;
}

/**
 * The chips for one finished turn, in the order they should be offered, or none.
 *
 * Deliberately silent on: approval requests (the approval card is the next step, and a
 * chip beside it competes with the decision being asked for), clarifications (they carry
 * their own options), refusals, out-of-scope replies and small talk. Being asked "what
 * next?" by a reply that just declined to answer reads as the product not listening.
 */
export function followUpsFor(response: AgentResponse): FollowUp[] {
  if (response.pending_action) return [];
  if (response.response_type !== "rows_answer") return [];

  const out: FollowUp[] = [];
  const seen = new Set<string>();
  const add = (chip: FollowUp | null) => {
    if (!chip || seen.has(chip.text) || out.length >= MAX_FOLLOW_UPS) return;
    seen.add(chip.text);
    out.push(chip);
  };

  const tool = plannedTool(response);
  const facts = resultFacts(response);

  // An invoice figure with the account code and period the tool reported against it is
  // exactly what the printable statement needs, so the offer is the statement.
  if (tool === "get_billing_summary") {
    const account = text(facts.account_code, ACCOUNT_CODE);
    const period = text(facts.period, PERIOD);
    if (account && period) {
      add({
        text: `Generate an invoice statement for ${account} for ${period}`,
        values: [account, period],
      });
    }
  }

  // Rows from an availability lookup are the free windows themselves — see
  // _rows_from_tool_result in server/agent/data.py, which keeps free_slots as the rows
  // and everything else as result facts. So the first row is a slot that can be booked,
  // and the chip books that slot rather than a window someone typed.
  if (tool === "check_availability") {
    const instrument = text(facts.instrument_name, NAME);
    const row = firstRow(response);
    const from = instantParts(row?.starts_at);
    const to = instantParts(row?.ends_at);
    if (instrument && from && to && from.date === to.date) {
      add({
        text: `Book ${instrument} on ${from.date} from ${from.clock} to ${to.clock}`,
        values: [instrument, from.date, from.clock, to.clock],
      });
    }
  }

  if (tool === "get_my_bookings") {
    // Only when the answer is about one booking. A list of twenty holds twenty ids and
    // no reason to prefer any of them, and a chip that silently picks the first is the
    // UI choosing a fact on the reader's behalf.
    const row = response.rows.length === 1 ? firstRow(response) : null;
    const booking = text(row?.id, BOOKING_ID);
    if (booking) {
      add({
        text: `Generate a booking confirmation for ${booking}`,
        values: [booking],
      });
    }
    // The rules behind the list, from the shelf that can cite them. Carries no value out
    // of the payload because it quotes none.
    add({ text: "What does the cancellation policy say?", values: [] });
  }

  const card = isRecord(response.card) ? response.card : null;
  const kind = card && typeof card.kind === "string" ? card.kind : null;
  const items = card && Array.isArray(card.items) ? card.items : [];

  if (kind === "facilities") {
    // One facility named in the card is one facility to ask about. Several, and there is
    // no "it" to point at, so the offer moves to the thing that covers all of them.
    if (items.length === 1) {
      const first = isRecord(items[0]) ? items[0] : null;
      const name = text(first?.title, NAME);
      if (name) {
        add({ text: `What instruments does the ${name} have?`, values: [name] });
      }
    }
    add({ text: "Generate a facility directory", values: [] });
  }

  if (kind === "instruments") {
    // The goal is the tool's own normalisation of what was asked ("image"), quoted so
    // the reader can see the report will be about the goal the system understood rather
    // than the sentence they typed.
    const goal = text(facts.goal, NAME);
    if (goal) {
      add({ text: `Generate a capability report for "${goal}"`, values: [goal] });
    }
  }

  return out;
}
