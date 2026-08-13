/**
 * Runs the shipped follow-up derivation over payloads captured from the running API.
 *
 * Not part of `npm test`: it needs a live server and a seeded database, so it is a probe
 * run by hand (`node ui/test/live-probe.ts <captured.json>`) when the rules are changed.
 * The committed fixtures in payloads.ts are what the suite asserts against; this is how
 * those fixtures are checked against reality rather than against themselves.
 */
import { readFileSync } from "node:fs";
import { followUpsFor } from "../src/followups.ts";
import type { AgentResponse } from "../src/types.ts";

interface Capture {
  handle: string;
  question: string;
  response: AgentResponse;
}

const captures: Capture[] = JSON.parse(readFileSync(process.argv[2], "utf8"));
let failures = 0;

for (const capture of captures) {
  const chips = followUpsFor(capture.response);
  const haystack = JSON.stringify(capture.response);
  console.log(`\n[${capture.handle}] ${capture.question}`);
  console.log(`  type=${capture.response.response_type} rows=${capture.response.rows.length}`);
  if (!chips.length) {
    console.log("  (no chips)");
    continue;
  }
  for (const chip of chips) {
    const invented = chip.values.filter((v) => !haystack.includes(v));
    if (invented.length) {
      failures += 1;
      console.log(`  CHIP  ${chip.text}`);
      console.log(`  !!!! INVENTED VALUES: ${JSON.stringify(invented)}`);
    } else {
      console.log(`  CHIP  ${chip.text}`);
    }
  }
}

console.log(failures ? `\nFAIL: ${failures} chip(s) quoted invented values` : "\nOK: every chip value came out of its payload");
process.exit(failures ? 1 : 0);
