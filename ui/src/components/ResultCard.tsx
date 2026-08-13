import { cellValue } from "../cells";
import type { Card, CardItem, CardKind } from "../types";

const KIND_LABEL: Record<CardKind, string> = {
  invoice: "Invoice",
  facilities: "Facilities",
  instruments: "Instruments",
  booking: "Booking",
  document: "Document",
  usage: "Usage",
};

function Tags({ item }: { item: CardItem }) {
  /* A facilities card with no distance to show falls back to the instrument count for
     its headline figure, and that same string is also the first meta entry — so the
     card printed "1 instrument" twice, side by side. Dropping a chip whose text the
     headline already carries character-for-character removes a repetition, never a
     fact: the string is still on screen, once. */
  const meta = item.meta.filter((entry) => entry !== item.value);
  if (!item.badges.length && !meta.length) return null;
  return (
    <div className="card-item-tags">
      {item.badges.map((badge, i) => (
        <span key={`b${i}`} className={`tone tone--${badge.tone}`}>
          {badge.text}
        </span>
      ))}
      {meta.map((entry, i) => (
        <span key={`m${i}`} className="meta-chip">
          {entry}
        </span>
      ))}
    </div>
  );
}

/**
 * Lays out a card exactly as its tool produced it: no value is parsed, rounded, unit-
 * converted or re-ordered on the way to the screen.
 *
 * The one thing that varies is weight. A facilities card exists because someone asked
 * where the nearest core that does X is, so on that card the distance and the location
 * line are typed up rather than down — a reader who has to hunt for the distance in a
 * row of grey chips has been handed the rows back.
 */
export function ResultCard({ card }: { card: Card }) {
  const located = card.kind === "facilities";

  return (
    <section className={`card-panel card-panel--${card.kind}`} aria-label={card.title}>
      <header className="card-head">
        <span className="card-kind">{KIND_LABEL[card.kind]}</span>
        <h3 className="card-title">{card.title}</h3>
        {card.subtitle && <p className="card-subtitle">{card.subtitle}</p>}
      </header>

      {card.fields.length > 0 && (
        <dl className="card-fields">
          {card.fields.map((f, i) => {
            /* A label with an empty value beside it is the worst shape this card can
               take: it looks like a figure that rendered to nothing, and the reader has
               no way to tell that apart from a figure of nothing. Absence gets a word. */
            const value = cellValue(f.value);
            return (
              <div key={`f${i}`} className={`card-field${f.emphasis ? " is-emphasis" : ""}`}>
                <dt>{f.label}</dt>
                <dd className={value.missing ? "cell-missing" : undefined}>{value.text}</dd>
              </div>
            );
          })}
        </dl>
      )}

      {card.items.length > 0 && (
        <ul className={`card-items${located ? " card-items--located" : ""}`}>
          {card.items.map((item, i) => (
            <li key={`i${i}`} className="card-item">
              <div className="card-item-main">
                <span className="card-item-title">{item.title}</span>
                {item.subtitle && <span className="card-item-subtitle">{item.subtitle}</span>}
                <Tags item={item} />
              </div>
              {item.value && <span className="card-item-value">{item.value}</span>}
            </li>
          ))}
        </ul>
      )}

      {card.footer && <footer className="card-footer">{card.footer}</footer>}
    </section>
  );
}
