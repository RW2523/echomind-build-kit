import { test } from "node:test";
import assert from "node:assert/strict";
import { NOT_RECORDED, cellValue, columnHeading, isDerivedColumn } from "../src/cells.ts";
import { CAPTURED } from "./payloads.ts";

test("a value the platform did not send is named, never left blank", () => {
  /* An empty cell and a cell whose value failed to render look identical, and one of them
     is a lie about the records. */
  for (const absent of [null, undefined, "", "   ", "\n"]) {
    assert.deepEqual(cellValue(absent), { text: NOT_RECORDED, missing: true });
  }
});

test("a recorded zero is still a zero", () => {
  /* The rule cuts both ways: showing 0 for "not recorded" is the failure it is usually
     stated as, and showing "not recorded" for a real zero — an instrument with no
     downtime, a month with no charges — is the same failure in the other direction. */
  assert.deepEqual(cellValue(0), { text: "0", missing: false });
  assert.deepEqual(cellValue("0"), { text: "0", missing: false });
  assert.deepEqual(cellValue(false), { text: "false", missing: false });
  assert.deepEqual(cellValue(0.0), { text: "0", missing: false });
});

test("a value is shown as it arrived, not reformatted", () => {
  /* Formatting is allowed; arithmetic is not, and neither is tidying. A figure the
     browser rewrote is a figure no tool stands behind. */
  assert.equal(cellValue("999.00").text, "999.00");
  assert.equal(cellValue(42.5).text, "42.5");
  assert.equal(cellValue("ACC-A1").text, "ACC-A1");
  assert.equal(cellValue("in_prep").text, "in_prep");
  /* A period is not an instant and keeps its own spelling. */
  assert.equal(cellValue("2026-03").text, "2026-03");
});

test("an ISO instant is read as a time, because ISO is how it is stored", () => {
  /* The one conversion, and the exception that proves the rule above: a booking row read
     "2026-08-17T08:00:00+00:00 to 2026-08-17T20:00:00+00:00", which says the same thing
     as "17 Aug 2026, 08:00 UTC" and takes a second reading to get there. Nothing about
     the moment changes — only how it is spelled. */
  assert.equal(cellValue("2026-03-30T01:00:00+00:00").text, "30 Mar 2026, 01:00 UTC");
  assert.equal(cellValue("2026-08-17T20:00:00Z").text, "17 Aug 2026, 20:00 UTC");
  /* A bare date has no instant to convert and keeps its day. */
  assert.equal(cellValue("2026-08-17").text, "17 Aug 2026");
  /* An offset is converted, never relabelled: 08:00+05:30 is 02:30 UTC, and stamping
     "UTC" on the wall clock would state a time that is simply wrong. */
  assert.equal(cellValue("2026-08-17T08:00:00+05:30").text, "17 Aug 2026, 02:30 UTC");
});

test("a structured cell shows its contents rather than [object Object]", () => {
  /* The facilities row really does carry its instruments as a list of objects, and the
     table printed the literal "[object Object]" over the top of them — neither the data
     nor an admission that it is missing. */
  const facilities = CAPTURED.facilities.payload;
  const cell = cellValue(facilities.rows[0].instruments);
  assert.ok(!cell.missing);
  assert.ok(cell.text.includes("Cryo-EM Titan"), cell.text.slice(0, 80));
  assert.deepEqual(cellValue([]), { text: "[]", missing: false });
});

test("the empty string a flattened list arrives as is treated as absence", () => {
  /* A usage row for a month with nothing in it comes back with `rows` as "" — the
     server's join of an empty list. Rendered as nothing at all it reads as a value that
     went missing between the API and the screen. */
  const usage = CAPTURED.labUsage.payload;
  assert.deepEqual(cellValue(usage.rows[0].rows), { text: NOT_RECORDED, missing: true });
  assert.deepEqual(cellValue(usage.rows[0].scheduled_hours), { text: "0", missing: false });
});

test("only the platform's own derived_ prefix marks a column as derived", () => {
  /* reference.v_devices publishes derived_half_day_rate and derived_day_rate as computed
     figures and says so in the migration. Nothing else is guessed at: a figure whose
     provenance this UI cannot see is left unlabelled rather than labelled hopefully. */
  assert.ok(isDerivedColumn("derived_day_rate"));
  assert.ok(!isDerivedColumn("hourly_rate"));
  assert.ok(!isDerivedColumn("total_downtime"));
  assert.ok(!isDerivedColumn("scheduled_hours"));
  assert.deepEqual(columnHeading("derived_half_day_rate"), {
    label: "half day rate",
    derived: true,
  });
  assert.deepEqual(columnHeading("account_code"), { label: "account code", derived: false });
});

test("no column of any captured answer is marked derived by accident", () => {
  /* A false "derived" mark is as misleading as a missing one: it tells the reader not to
     quote a figure the facility does publish. */
  for (const [name, captured] of Object.entries(CAPTURED)) {
    for (const column of captured.payload.columns) {
      assert.ok(!isDerivedColumn(column), `${name} column ${column} should not be marked derived`);
    }
  }
});
