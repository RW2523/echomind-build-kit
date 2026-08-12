const EM_DASH = "—";
const EN_DASH = "–";

/** Splits on the separators the clarify sentence uses: ", " between items and " or "
 *  before the last one. The comma branch swallows a following "or" so that the Oxford
 *  form ", or B" yields one separator rather than a separator and a stray "or B".
 *  Case-insensitive so "OR" does not silently produce a single option. */
const SEPARATORS = /\s*,\s*(?:or\s+)?|\s+or\s+/i;

const MAX_OPTIONS = 6;
const MAX_OPTION_LENGTH = 60;

/**
 * The choices offered here are names — an instrument, a booking, a core — and names in
 * this domain do not run past a handful of words. A long fragment is the tell that the
 * split landed inside a clause rather than between two choices, which is the failure
 * that turns "…, or shall I list them all?" into a button reading "shall I list them
 * all". One over-long member disqualifies the whole list, because a list that is half
 * choices and half sentence fragments is worse than no buttons at all.
 */
const MAX_OPTION_WORDS = 6;

/**
 * Pulls the choices out of a clarify question shaped "Which one — A or B?", returning
 * them in the order they were offered, or an empty list when the sentence does not
 * clearly hold a list of choices.
 *
 * Refusing to guess is the point. These strings are sent back as the user's next message,
 * so a mis-split turns a clarification into a question nobody asked — "Which booking —
 * the one at 09:00, or shall I list them?" must not become a button reading "shall I list
 * them". Anything the pattern does not cover cleanly falls through to the plain question,
 * which the user can still answer by typing.
 */
export function clarifyOptions(text: string): string[] {
  const end = text.lastIndexOf("?");
  if (end < 0) return [];

  const question = text.slice(0, end);
  const dash = Math.max(question.lastIndexOf(EM_DASH), question.lastIndexOf(EN_DASH));
  if (dash < 0) return [];

  const parts = question
    .slice(dash + 1)
    .split(SEPARATORS)
    .map((part) => part.trim().replace(/[.;:]+$/, "").trim());

  const options: string[] = [];
  const seen = new Set<string>();
  for (const part of parts) {
    // A fragment carrying its own punctuation is a clause, not a choice.
    if (!part || part.length > MAX_OPTION_LENGTH) return [];
    if (part.split(/\s+/).length > MAX_OPTION_WORDS) return [];
    if (/[?!\n]/.test(part) || part.includes(EM_DASH) || part.includes(EN_DASH)) return [];
    const key = part.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    options.push(part);
  }

  if (options.length < 2 || options.length > MAX_OPTIONS) return [];
  return options;
}
