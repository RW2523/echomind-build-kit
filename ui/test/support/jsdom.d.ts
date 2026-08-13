/**
 * Just enough of jsdom for `tsc -b` to check the DOM suite.
 *
 * Hand-written for the same reason node-test.d.ts is: @types/jsdom depends on @types/node,
 * and pulling those into this compile puts Node's globals into a browser application —
 * `setTimeout` starts returning a Timeout instead of a number, and `process` becomes
 * something the app could plausibly reference. Only the two members the harness touches
 * are declared. If either drifts from the real API, the suite says so when node runs it.
 */
declare module "jsdom" {
  export interface JSDOMOptions {
    url?: string;
    /** Provides requestAnimationFrame — and the timer that has to be closed afterwards. */
    pretendToBeVisual?: boolean;
  }

  export class JSDOM {
    constructor(html?: string, options?: JSDOMOptions);
    readonly window: Window & typeof globalThis & { close(): void };
  }
}
