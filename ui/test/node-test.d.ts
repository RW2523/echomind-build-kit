/**
 * Just enough of Node's test runner and assertion library for `tsc -b` to check the
 * tests beside the code they test.
 *
 * Written out here rather than pulled in with @types/node on purpose. Installing those
 * types puts Node's globals into the compile of a browser application — where `setTimeout`
 * starts returning a Timeout object instead of a number and `process` becomes something
 * the app could plausibly reference. The tests need two modules, not a second runtime, so
 * that is what is declared. If a signature here drifts from the real one, the test run
 * itself says so: node is the thing that actually executes them.
 */

declare module "node:test" {
  export function test(name: string, fn: () => void | Promise<void>): void;
  /** Runs once after the file's tests. The DOM suite closes its jsdom window here. */
  export function after(fn: () => void | Promise<void>): void;
}

declare module "node:assert/strict" {
  interface Assert {
    (value: unknown, message?: string): asserts value;
    equal(actual: unknown, expected: unknown, message?: string): void;
    notEqual(actual: unknown, expected: unknown, message?: string): void;
    deepEqual(actual: unknown, expected: unknown, message?: string): void;
    ok(value: unknown, message?: string): asserts value;
    match(value: string, pattern: RegExp, message?: string): void;
    fail(message?: string): never;
  }
  const assert: Assert;
  export default assert;
}
