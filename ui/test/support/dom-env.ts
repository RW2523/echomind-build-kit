/**
 * A browser for the tests to run in, installed before anything else is loaded.
 *
 * This is a module of its own, and it is imported first, for one non-obvious reason:
 * react-dom decides at load time whether it is running in a browser and which event
 * plugins it needs, caching the answers in module scope. Set the globals after react-dom
 * has been required and it has already concluded there is no DOM — it then routes change
 * events through an Internet Explorer polyfill and dies on `activeElement.attachEvent is
 * not a function`, which reads as a bug in the component rather than in the harness.
 * Import order is the fix, so the setup cannot live inside the test file that needs it.
 *
 * It lives under test/support/ rather than beside the tests because tests/test_ui_units.py
 * requires every file directly in ui/test to be one the runner's glob picks up — a helper
 * sitting there would look like a test nobody runs.
 */
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost:5173/",
  pretendToBeVisual: true,
});

export const win = dom.window as unknown as Window & typeof globalThis;

const globals = globalThis as Record<string, unknown>;

/* defineProperty rather than assignment: `navigator` and `location` are getter-only on
   Node's global object, and a plain assignment throws before a single test runs. */
for (const key of [
  "window", "document", "navigator", "location", "localStorage", "getComputedStyle",
  "HTMLElement", "HTMLTextAreaElement", "HTMLButtonElement", "Element", "Node",
  "Event", "KeyboardEvent", "MouseEvent", "CustomEvent", "requestAnimationFrame",
  "cancelAnimationFrame",
]) {
  Object.defineProperty(globals, key, {
    value: (win as unknown as Record<string, unknown>)[key],
    writable: true,
    configurable: true,
  });
}

/* jsdom implements neither of these, and App reaches for both: matchMedia decides whether
   the rail is a drawer, and the transcript scrolls itself after every turn. Left undefined
   they throw inside an effect, which React then reports against an unrelated component. */
const matchMedia = (query: string) =>
  ({
    matches: query.includes("min-width"),
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;

globals.matchMedia = matchMedia;
win.matchMedia = matchMedia;
win.Element.prototype.scrollTo = function scrollTo() {};
win.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};

/** React refuses to run its `act` bookkeeping without this, and warns on every update. */
globals.IS_REACT_ACT_ENVIRONMENT = true;
