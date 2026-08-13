/**
 * How one value out of a row, and one column heading, reach the screen.
 *
 * Both were inline in the two table renderers as `String(row[c] ?? "")`, which has two
 * failures a reader cannot see. A null printed an empty cell, indistinguishable from a
 * value the renderer dropped on the way — "verified or silent" means absence has to be
 * stated, not left as whitespace. And a column holding a list of objects — the
 * `instruments` column of a facilities row — printed the literal `[object Object]`,
 * which is neither the data nor an admission that it is missing.
 *
 * Nothing here parses, rounds or converts a value. A string arrives as it was sent; a
 * number is stringified, not reformatted; a structure becomes its JSON. The only
 * judgement made is present versus absent, and it is made narrowly.
 */

/** The one word this UI uses for "the platform returned nothing here". */
export const NOT_RECORDED = "not recorded";

export interface Cell {
  text: string;
  /** True when `text` is our word for absence rather than something the server sent. */
  missing: boolean;
}

/**
 * Absence is exactly three things: null, undefined, and a string with nothing in it.
 *
 * Deliberately not on the list: 0, false, and "0". A recorded zero is a fact — an
 * instrument with no downtime, an invoice line of nothing — and hiding it behind "not
 * recorded" would be the same lie as the reverse, which the house rule names outright.
 * An empty array or object is likewise left alone: `[]` is cryptic but true, and this
 * function is not the place to decide it means "none".
 */
export function cellValue(raw: unknown): Cell {
  if (raw === null || raw === undefined) return { text: NOT_RECORDED, missing: true };
  if (typeof raw === "string") {
    return raw.trim() === "" ? { text: NOT_RECORDED, missing: true } : { text: raw, missing: false };
  }
  if (typeof raw === "object") {
    // A list or an object in a cell is still evidence, so it is shown rather than
    // flattened away — as JSON, the same choice the approval card already makes for a
    // nested payload, and for the same reason: [object Object] hides what it summarises.
    const json = JSON.stringify(raw);
    return json === undefined
      ? { text: NOT_RECORDED, missing: true }
      : { text: json, missing: false };
  }
  return { text: String(raw), missing: false };
}

/**
 * Columns the platform itself names as derived.
 *
 * `reference.v_devices` publishes `derived_half_day_rate` and `derived_day_rate`, and
 * db/migrations/009_domain_spaces.sql says why in as many words: they are hourly_rate
 * times four and eight, "derived here, not a tariff the facility publishes". The prefix
 * is the server's own flag, so marking it in the table surfaces a distinction that
 * already exists in the payload. No other notion of "derived" is invented: the card
 * contract carries none, and a figure this UI cannot see the provenance of is left
 * unlabelled rather than guessed at.
 */
export function isDerivedColumn(name: string): boolean {
  return name.startsWith("derived_");
}

export interface Heading {
  label: string;
  derived: boolean;
}

/** `derived_day_rate` reads as "day rate", with the flag carried separately so the
 *  renderer can show it as a mark rather than as part of the name. */
export function columnHeading(name: string): Heading {
  const derived = isDerivedColumn(name);
  const bare = derived ? name.slice("derived_".length) : name;
  return { label: bare.replace(/_/g, " "), derived };
}
