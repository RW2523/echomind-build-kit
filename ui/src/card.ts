import type { BadgeTone, Card, CardBadge, CardField, CardItem, CardKind } from "./types";

const KINDS: readonly string[] = [
  "invoice",
  "facilities",
  "instruments",
  "booking",
  "document",
  "usage",
];

const TONES: readonly string[] = ["ok", "warn", "info", "muted"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** A contract field that may be null but must not be a number, object or undefined. */
function nullableStr(value: unknown): { ok: true; value: string | null } | { ok: false } {
  if (value === null || value === undefined) return { ok: true, value: null };
  if (typeof value === "string") return { ok: true, value };
  return { ok: false };
}

function field(raw: unknown): CardField | null {
  if (!isRecord(raw)) return null;
  const label = str(raw.label);
  const value = str(raw.value);
  if (label === null || value === null) return null;
  return { label, value, emphasis: raw.emphasis === true };
}

function badge(raw: unknown): CardBadge | null {
  if (!isRecord(raw)) return null;
  const text = str(raw.text);
  const tone = str(raw.tone);
  if (text === null || tone === null || !TONES.includes(tone)) return null;
  return { text, tone: tone as BadgeTone };
}

function item(raw: unknown): CardItem | null {
  if (!isRecord(raw)) return null;
  const title = str(raw.title);
  if (title === null) return null;

  const subtitle = nullableStr(raw.subtitle);
  const value = nullableStr(raw.value);
  if (!subtitle.ok || !value.ok) return null;

  const metaRaw = raw.meta === undefined || raw.meta === null ? [] : raw.meta;
  if (!Array.isArray(metaRaw)) return null;
  const meta: string[] = [];
  for (const entry of metaRaw) {
    const text = str(entry);
    if (text === null) return null;
    meta.push(text);
  }

  const badgesRaw = raw.badges === undefined || raw.badges === null ? [] : raw.badges;
  if (!Array.isArray(badgesRaw)) return null;
  const badges: CardBadge[] = [];
  for (const entry of badgesRaw) {
    const parsed = badge(entry);
    if (parsed === null) return null;
    badges.push(parsed);
  }

  return { title, subtitle: subtitle.value, meta, badges, value: value.value };
}

/**
 * Returns the payload as a Card only when every part of it matches the contract, and
 * null otherwise — the caller then falls back to prose plus the evidence table.
 *
 * Partial rendering is the failure worth preventing here. A card whose badges arrived
 * malformed still draws a confident panel, and a reader cannot see that a status is
 * missing from a layout they have never seen complete. Dropping to the table loses the
 * polish but never loses a fact, so the whole card is refused rather than patched with
 * placeholder values the tool layer never produced.
 */
export function asCard(raw: unknown): Card | null {
  if (!isRecord(raw)) return null;

  const kind = str(raw.kind);
  const title = str(raw.title);
  if (kind === null || !KINDS.includes(kind) || title === null) return null;

  const subtitle = nullableStr(raw.subtitle);
  const footer = nullableStr(raw.footer);
  if (!subtitle.ok || !footer.ok) return null;

  const fieldsRaw = raw.fields === undefined || raw.fields === null ? [] : raw.fields;
  const itemsRaw = raw.items === undefined || raw.items === null ? [] : raw.items;
  if (!Array.isArray(fieldsRaw) || !Array.isArray(itemsRaw)) return null;

  const fields: CardField[] = [];
  for (const entry of fieldsRaw) {
    const parsed = field(entry);
    if (parsed === null) return null;
    fields.push(parsed);
  }

  const items: CardItem[] = [];
  for (const entry of itemsRaw) {
    const parsed = item(entry);
    if (parsed === null) return null;
    items.push(parsed);
  }

  // A card with a title and nothing under it is an empty frame around no evidence.
  if (!fields.length && !items.length) return null;

  return {
    kind: kind as CardKind,
    title,
    subtitle: subtitle.value,
    fields,
    items,
    footer: footer.value,
  };
}
