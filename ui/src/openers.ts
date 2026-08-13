import type { DemoUser } from "./types";

/**
 * The questions offered on an empty thread.
 *
 * These are prompts, not permissions. Nothing here decides what anyone may see — every
 * one of them goes through the same router, the same tool tiers and the same retrieval
 * filter as a typed question, and bob's opener is on the list precisely because it comes
 * back refused. Choosing what to *suggest* by role is a different act from choosing what
 * to *show*, and only the server does the second one.
 */
export interface Opener {
  text: string;
  /** What the question demonstrates. Written by the UI, never sourced from an answer. */
  note: string;
}

/**
 * The four openers, in the order a first-time reader should meet them: find a place,
 * choose an instrument, see your own money, read the policy behind a charge.
 */
export const BASE_OPENERS: Opener[] = [
  // No opener carries a location, so no answer to one can carry a distance. The note
  // said "with distance" and the card that came back never had one — a promise the
  // product broke in the first thing a new reader clicks.
  { text: "Where is the nearest core that can do cryo-EM?", note: "Facilities · where and what" },
  { text: "I want to image live cells — what should I use?", note: "Instruments · by technique" },
  { text: "What is on my March invoice?", note: "Invoice · your charges only" },
  {
    text: "What am I charged if I cancel a booking 12 hours before it starts?",
    note: "Policy · with the passage",
  },
];

/** Each identity keeps one opener that only makes sense as that person — the refusal for
 *  Bob, the lab-wide read for Asha, the approval for Cora. That is the demo's whole point. */
const BY_HANDLE: Record<string, Opener[]> = {
  alice: BASE_OPENERS,
  bob: [
    ...BASE_OPENERS.slice(0, 3),
    { text: "Show me alice's bookings", note: "Permissions · refused, not answered" },
  ],
  asha: [
    ...BASE_OPENERS.slice(0, 2),
    { text: "Why was lab A charged $412 in March?", note: "Usage · traced to the bookings" },
    { text: "Show me lab A's usage this month", note: "Usage · your labs only" },
  ],
  cora: [
    ...BASE_OPENERS.slice(0, 2),
    {
      text: "Which instrument had the most downtime in March 2026?",
      note: "Usage · across all cores",
    },
    { text: "Generate the monthly summary for 2026-03", note: "Document · waits for approval" },
  ],
};

/**
 * Openers for someone the demo does not know by name.
 *
 * The four demo identities have hand-written lists that name their own labs; a real
 * signed-in PI has no lab A, so the role lists ask the same *kind* of question without
 * naming anybody's records. A user gets the base four unchanged.
 */
const BY_ROLE: Record<string, Opener[]> = {
  pi: [
    ...BASE_OPENERS.slice(0, 2),
    { text: "Show me my lab's usage this month", note: "Usage · your labs only" },
    { text: "What is on my March invoice?", note: "Invoice · your charges only" },
  ],
  admin: [
    ...BASE_OPENERS.slice(0, 2),
    {
      text: "Which instrument had the most downtime in March 2026?",
      note: "Usage · across all cores",
    },
    { text: "Generate the monthly summary for 2026-03", note: "Document · waits for approval" },
  ],
};

/** Handle first, then role, then the base four. Nobody is ever offered nothing. */
export function openersFor(user: Pick<DemoUser, "handle" | "role"> | null): Opener[] {
  if (!user) return BASE_OPENERS;
  return BY_HANDLE[user.handle] ?? BY_ROLE[user.role] ?? BASE_OPENERS;
}
