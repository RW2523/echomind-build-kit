import { test } from "node:test";
import assert from "node:assert/strict";
import { citationCopyText } from "../src/clipboard.ts";
import type { Citation } from "../src/types.ts";

const citation: Citation = {
  index: 2,
  doc_id: "doc-booking-and-cancellation-rules-v1-6",
  breadcrumb: "Booking and Cancellation Rules > Cancellation (v1.6)",
  title: "Booking and Cancellation Rules",
  chunk_id: 2650,
  score: 0.6583,
};

test("a copied citation carries the passage and where it came from", () => {
  /* Pasted into a ticket or an email, a quotation is only worth anything if the person
     receiving it can find the same clause. */
  const copied = citationCopyText(citation, "Cancellations inside 24 hours are charged at 50%.");
  assert.ok(copied.includes("[2] Booking and Cancellation Rules"));
  assert.ok(copied.includes("Cancellation (v1.6)"));
  assert.ok(copied.includes("Cancellations inside 24 hours are charged at 50%."));
});

test("the passage is copied verbatim, never tightened", () => {
  /* A quotation that has been edited is no longer the source it names. */
  const passage = "Line one.\n\n  Line two, indented.\nLine three.";
  assert.ok(citationCopyText(citation, passage).includes(passage.trim()));
});

test("a citation whose passage never loaded says so instead of pretending", () => {
  /* The reference is still worth copying — but pasting a heading alone would look like a
     quotation that came with evidence behind it. */
  const copied = citationCopyText(citation, "");
  assert.ok(copied.includes("(passage not loaded)"));
});
