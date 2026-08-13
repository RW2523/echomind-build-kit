"""The UI's pure logic is tested here too, so `make test` is still the one gate.

The bug that motivated this file shipped in a session where every server test passed: a
modal was imported into a component and never rendered, so a button did nothing and
nothing anywhere went red. Server tests cannot see that class of fault, and a front-end
test suite nobody runs is the same as not having one — so the UI's unit tests are run by
pytest, from the same command that runs everything else.

They are Node's own test runner over the TypeScript sources, with no test framework added
to ui/package.json — this repo pins its stack. `npm test` runs three things:

  test:units   the pure decisions — which chips an answer earns, what an absent value
               renders as, what a keypress means. Node strips the types and runs them.
  test:render  the real components through react-dom/server, reading the markup. This is
               the only check that can see a component built, imported, and never drawn.
  test:dom     the real App in a jsdom browser, driven by real events over the real
               api.ts with only `fetch` stubbed. It covers the seam the other two cannot:
               whether a keypress, a click or a focus hand-off is actually wired to the
               decision that was unit-tested. Escape-to-stop and the follow-up chips are
               verified here rather than by hand.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from server.config import REPO_ROOT

UI = REPO_ROOT / "ui"

needs_node = pytest.mark.skipif(
    shutil.which("npm") is None or not (UI / "node_modules").is_dir(),
    reason="ui dependencies are not installed — run `cd ui && npm install`",
)


@needs_node
def test_the_ui_tests_pass():
    """Every pure decision the chat surface makes — which follow-up chips an answer earns,
    what an absent value renders as, what a keypress in the composer means — has a test
    beside it, and this is what makes those tests part of the suite rather than a file
    someone remembers to run.
    """
    result = subprocess.run(
        ["npm", "test", "--silent"],
        cwd=UI,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@needs_node
def test_the_ui_test_files_are_all_reachable_from_npm_test():
    """A test file the runner's glob does not match is a test file nobody runs.

    The suite above reports success on whatever it happened to load, so a new test named
    slightly wrong — `followups.spec.ts`, `composer.tests.ts` — passes silently by not
    existing as far as node is concerned. This pins the naming to the script that runs it.
    """
    import json

    scripts = json.loads((UI / "package.json").read_text(encoding="utf-8"))["scripts"]
    # Every script is scanned rather than a named few: a test file added together with a
    # new runner script it is the only user of would otherwise be checked against a list
    # that does not mention it, and pass by being invisible to both.
    ran = set()
    for command in scripts.values():
        for part in command.split():
            if part.startswith("test/"):
                ran.update(p.name for p in UI.glob(part))

    present = {
        p.name
        for p in (UI / "test").iterdir()
        if p.suffix in {".ts", ".tsx"} and not p.name.endswith(".d.ts") and p.name != "payloads.ts"
    }
    assert present, "ui/test holds no tests at all"
    assert present <= ran, f"these ui test files are never run: {sorted(present - ran)}"
