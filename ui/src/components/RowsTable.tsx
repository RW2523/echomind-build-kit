import { cellValue, columnHeading } from "../cells";

/**
 * The rows behind an answer, in one table both the evidence preview and the approval
 * card draw.
 *
 * Two copies of this markup existed and they disagreed: one of them printed
 * `[object Object]` for a facilities row's instrument list, and neither said anything at
 * all when a value was null — the cell was simply empty, which reads as "nothing was
 * charged" rather than "nothing was recorded". A single renderer means an improvement to
 * how absence is shown reaches every place rows are shown.
 *
 * A result with many columns is wide, and it stays wide: nothing is dropped or truncated
 * to make it fit, because a column hidden to save space is a column the reader did not
 * know they were not seeing. The width is absorbed by the container, which scrolls on
 * its own — the page behind it never moves sideways.
 */
export function RowsTable({
  columns,
  rows,
  label,
}: {
  columns?: string[];
  rows: Record<string, unknown>[];
  /** Describes the table to a screen reader; the visible caption is the caller's job. */
  label?: string;
}) {
  if (!rows.length) return null;
  const shown = columns?.length ? columns : Object.keys(rows[0]);

  return (
    <div className="table-wrap">
      <table aria-label={label}>
        <thead>
          <tr>
            {shown.map((column) => {
              const heading = columnHeading(column);
              return (
                <th key={column} scope="col">
                  {heading.label}
                  {heading.derived && (
                    /* The platform publishes this column under a `derived_` name because
                       the figure is computed from another column rather than stored —
                       see db/migrations/009_domain_spaces.sql. Carrying that mark to the
                       screen keeps a reader from quoting it back as a published rate. */
                    <span
                      className="col-derived"
                      title="Computed by the platform from another column, not a stored figure."
                    >
                      derived
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {shown.map((column) => {
                const cell = cellValue(row[column]);
                return (
                  <td key={column} className={cell.missing ? "cell-missing" : undefined}>
                    {cell.text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
