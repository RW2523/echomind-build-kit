import { test } from "node:test";
import assert from "node:assert/strict";
import { BASE_OPENERS, openersFor } from "../src/openers.ts";

test("each demo identity keeps the opener that only makes sense as that person", () => {
  /* The refusal for Bob, the lab-wide read for Asha, the approval for Cora: those three
     openers are the demo's whole point, and a refactor that quietly replaced them with a
     generic list would remove the thing the product is being shown for. */
  const bob = openersFor({ handle: "bob", role: "user" }).map((o) => o.text);
  assert.ok(bob.includes("Show me alice's bookings"));

  const asha = openersFor({ handle: "asha", role: "pi" }).map((o) => o.text);
  assert.ok(asha.includes("Show me lab A's usage this month"));

  const cora = openersFor({ handle: "cora", role: "admin" }).map((o) => o.text);
  assert.ok(cora.includes("Generate the monthly summary for 2026-03"));
});

test("someone the demo does not know by name is offered questions about their own records", () => {
  /* The handwritten lists name lab A. A real signed-in PI has no lab A, and an opener
     that refuses the moment it is clicked is a bad first impression of a product whose
     point is that it answers what it can. */
  const pi = openersFor({ handle: "someone-else", role: "pi" }).map((o) => o.text);
  assert.ok(pi.includes("Show me my lab's usage this month"));
  assert.ok(!pi.some((text) => text.includes("lab A")));

  const admin = openersFor({ handle: "another", role: "admin" }).map((o) => o.text);
  assert.ok(admin.includes("Generate the monthly summary for 2026-03"));
});

test("nobody is ever offered nothing", () => {
  /* An empty thread with no openers is a blank page with a text box on it. */
  for (const user of [
    null,
    { handle: "", role: "" },
    { handle: "unknown", role: "user" },
    { handle: "unknown", role: "curator" },
  ]) {
    const openers = openersFor(user);
    assert.ok(openers.length >= 3, JSON.stringify(user));
    for (const opener of openers) {
      assert.ok(opener.text.trim().length > 0);
      assert.ok(opener.note.trim().length > 0);
    }
  }
  assert.deepEqual(openersFor({ handle: "unknown", role: "user" }), BASE_OPENERS);
});
