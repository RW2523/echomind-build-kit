"""The UI's pure logic is tested here too, so `make test` is still the one gate.

The bug that motivated this file shipped in a session where every server test passed: a
modal was imported into a component and never rendered, so a button did nothing and
nothing anywhere went red. Server tests cannot see that class of fault, and a front-end
test suite nobody runs is the same as not having one — so the UI's unit tests are run by
pytest, from the same command that runs everything else.

They are Node's own test runner over the TypeScript sources, with no test framework added
to ui/package.json — this repo pins its stack, and a chip-derivation function does not
need a browser, a DOM shim or a runner to be tested. `npm test` runs two things: the pure
decisions (node strips the types and runs them directly), and a render smoke test that
puts the real components through react-dom/server and reads the markup, which is the only
check that can see a component built, imported, and never actually drawn.
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
    ran = set()
    for name in ("test:units", "test:render"):
        for part in scripts[name].split():
            if part.startswith("test/"):
                ran.update(p.name for p in UI.glob(part))

    present = {
        p.name
        for p in (UI / "test").iterdir()
        if p.suffix in {".ts", ".tsx"} and not p.name.endswith(".d.ts") and p.name != "payloads.ts"
    }
    assert present, "ui/test holds no tests at all"
    assert present <= ran, f"these ui test files are never run: {sorted(present - ran)}"
