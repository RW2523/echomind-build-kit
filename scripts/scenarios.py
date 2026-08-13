"""Drive the assistant through realistic situations and record what it actually did.

This is not the test suite and not the eval. Those check units and answer quality; this
watches the whole machine work and writes down the evidence: which branch took the turn,
which tools ran, what SQL executed, what was proposed, what a human approved, and what
landed in the audit table.

Written because a green suite has repeatedly coexisted with a broken product in this
repo — a download button that could never authenticate, a Source popup that opened
nothing, a cancellation that quietly corrupted the seed. Each was invisible to the tests
and obvious the moment something drove the real thing end to end.

    make scenarios          # run them all, write scenario_reports/<date>.json

Every scenario declares what must be true of the response, so a run is pass/fail rather
than a wall of output somebody has to read. Read-only by default: scenarios that write
say so, and their effects are undone afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.mint_jwt import mint
from server.config import REPO_ROOT
from server.db import owner_engine, session_scope

# Through the dev server rather than the API, because two of the failures this file
# exists to catch lived in the seam between them and were invisible from the API side.
DEFAULT_BASE = "http://localhost:5173"
OUT_DIR = REPO_ROOT / "scenario_reports"

USERS = {
    "alice": {"id": "u-alice", "name": "Alice Nguyen", "role": "user",
              "lab_ids": ("lab-a",), "facility_ids": ()},
    "asha": {"id": "u-asha", "name": "Asha Patel", "role": "pi",
             "lab_ids": ("lab-a",), "facility_ids": ()},
    "bob": {"id": "u-bob", "name": "Bob Okafor", "role": "user",
            "lab_ids": ("lab-b",), "facility_ids": ()},
    "cora": {"id": "u-cora", "name": "Cora Diaz", "role": "admin",
             "lab_ids": (), "facility_ids": ("fac-imaging",)},
}


def headers(handle: str) -> dict[str, str]:
    u = USERS[handle]
    return {"Authorization": "Bearer " + mint(
        user_id=u["id"], name=u["name"], role=u["role"],
        lab_ids=u["lab_ids"], facility_ids=u["facility_ids"])}


# --- what one turn produced ----------------------------------------------------------


@dataclass
class Turn:
    """One question and everything observable about how it was answered."""

    who: str
    asked: str
    route: str | None = None
    response_type: str | None = None
    text: str = ""
    rows: int = 0
    columns: list[str] = field(default_factory=list)
    executed_sql: str | None = None
    citations: int = 0
    gate: dict[str, Any] | None = None
    faithfulness: dict[str, Any] | None = None
    card: str | None = None
    pending_action: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    seconds: float = 0.0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Scenario:
    name: str
    intent: str
    writes: bool = False
    turns: list[Turn] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.problems and not any(t.problems for t in self.turns)


class Session:
    """One conversation with the assistant, from one identity."""

    def __init__(self, base: str, who: str, scenario: Scenario):
        self.base, self.who, self.scenario = base, who, scenario
        self.thread: str | None = None

    def ask(self, message: str, expect: Callable[[Turn], None] | None = None) -> Turn:
        turn = Turn(who=self.who, asked=message)
        started = time.monotonic()
        try:
            r = requests.post(
                f"{self.base}/chat",
                json={"message": message,
                      **({"thread_id": self.thread} if self.thread else {})},
                headers=headers(self.who), timeout=300,
            )
            turn.seconds = round(time.monotonic() - started, 2)
            if r.status_code != 200:
                turn.problems.append(f"chat returned {r.status_code}")
                self.scenario.turns.append(turn)
                return turn
            body = r.json()
            self.thread = body.get("thread_id") or self.thread
            turn.route = body.get("route")
            turn.response_type = body.get("response_type")
            turn.text = body.get("text") or ""
            turn.rows = len(body.get("rows") or [])
            turn.columns = list(body.get("columns") or [])
            turn.executed_sql = body.get("executed_sql")
            turn.citations = len(body.get("citations") or [])
            turn.gate = body.get("gate")
            turn.faithfulness = body.get("faithfulness")
            card = body.get("card")
            turn.card = (card or {}).get("kind") if isinstance(card, dict) else None
            turn.pending_action = body.get("pending_action")
        except Exception as exc:  # a scenario must not take the run down with it
            turn.seconds = round(time.monotonic() - started, 2)
            turn.problems.append(f"{type(exc).__name__}: {exc}")
        if expect:
            try:
                expect(turn)
            except AssertionError as exc:
                turn.problems.append(str(exc))
        self.scenario.turns.append(turn)
        return turn

    def decide(self, turn: Turn, choice: str) -> dict[str, Any]:
        action = (turn.pending_action or {}).get("action_id")
        if not action:
            turn.problems.append(f"cannot {choice}: nothing was proposed")
            return {}
        r = requests.post(f"{self.base}/actions/{action}/{choice}",
                          headers=headers(self.who), timeout=300)
        body = r.json() if r.status_code == 200 else {"error": r.status_code}
        turn.decision = {"choice": choice, "status": body.get("status"),
                         "result": body.get("result")}
        return body


# --- the assertions scenarios are written in ------------------------------------------


def wants(turn: Turn, *, route: str | None = None, kind: str | None = None,
          says: tuple[str, ...] = (), never_says: tuple[str, ...] = (),
          rows_at_least: int | None = None, cited: bool = False) -> None:
    if route and turn.route != route:
        raise AssertionError(f"went to {turn.route}, expected {route}")
    if kind and turn.response_type != kind:
        raise AssertionError(f"answered as {turn.response_type}, expected {kind}")
    low = turn.text.lower()
    for phrase in says:
        if phrase.lower() not in low:
            raise AssertionError(f"never said {phrase!r}: {turn.text[:110]!r}")
    for phrase in never_says:
        if phrase.lower() in low:
            raise AssertionError(f"said {phrase!r}, which it must not: {turn.text[:110]!r}")
    if rows_at_least is not None and turn.rows < rows_at_least:
        raise AssertionError(f"returned {turn.rows} rows, wanted at least {rows_at_least}")
    if cited and not turn.citations:
        raise AssertionError("answered without a citation")


def figure_matches_the_ledger(turn: Turn, sql: str, params: dict) -> None:
    """The number in the sentence must be the number in the database.

    Golden rule 1 as an assertion: not "does it look plausible" but "is it the value the
    ledger holds". Anything else is a fluent guess.
    """
    with session_scope() as s:
        actual = s.execute(text(sql), params).scalar()
    if actual is None:
        raise AssertionError("the ledger has no such figure to compare against")
    plain = f"{float(actual):,.2f}"
    for candidate in (plain, plain.replace(",", ""), f"{float(actual):,.0f}",
                      f"{float(actual):.0f}"):
        if candidate in turn.text:
            return
    raise AssertionError(f"ledger says {plain}; the answer said {turn.text[:110]!r}")


# --- the scenarios ---------------------------------------------------------------------

SCENARIOS: list[Callable[[str], Scenario]] = []


def scenario(name: str, intent: str, writes: bool = False):
    def wrap(fn):
        def run(base: str) -> Scenario:
            sc = Scenario(name=name, intent=intent, writes=writes)
            started = time.monotonic()
            try:
                fn(Session(base, fn.__annotations__.get("who", "asha"), sc), sc, base)
            except Exception as exc:
                sc.problems.append(f"{type(exc).__name__}: {exc}")
            sc.seconds = round(time.monotonic() - started, 2)
            return sc
        run.__name__ = fn.__name__
        SCENARIOS.append(run)
        return run
    return wrap


@scenario("reading: a figure comes from the ledger",
          "A PI asks what a lab account was charged. The number must be the ledger's.")
def _billing(_s: Session, sc: Scenario, base: str) -> None:
    s = Session(base, "asha", sc)
    turn = s.ask("what was ACC-A1 charged in March 2026?",
                 lambda t: wants(t, route="data", rows_at_least=1))
    if not turn.problems:
        figure_matches_the_ledger(
            turn,
            "SELECT total FROM infinity.invoices WHERE account_code=:a AND period=:p",
            {"a": "ACC-A1", "p": "2026-03"},
        )


@scenario("reading: a knowledge answer is cited or withheld, never guessed",
          "Either the corpus supports every claim and the answer cites it, or the answer "
          "is refused. What must never happen is a fluent answer with no source.")
def _policy(_s: Session, sc: Scenario, base: str) -> None:
    s = Session(base, "alice", sc)

    def cited_or_withheld(t: Turn) -> None:
        if t.response_type == "redirect":
            return  # refused, which is the honest outcome when retrieval falls short
        wants(t, route="knowledge", cited=True)

    s.ask("what is the cancellation policy for bookings?", cited_or_withheld)
    # The same question the rules table answers, through the tool that reads it as data.
    s.ask("if I cancel my booking tomorrow, what does it cost?",
          lambda t: wants(t, never_says=("no charge for any",)))


@scenario("reading: the assistant refuses what it cannot know",
          "An off-topic question must be redirected, never answered plausibly.")
def _offtopic(_s: Session, sc: Scenario, base: str) -> None:
    s = Session(base, "alice", sc)
    s.ask("what is the capital of France?",
          lambda t: wants(t, never_says=("Paris",)))
    s.ask("what is the parking permit policy for visiting researchers?",
          lambda t: wants(t, never_says=("parking permit is",)))


@scenario("reading: a user cannot read another lab's figures",
          "Permission is enforced in the tool layer, not by asking the model nicely.")
def _isolation(_s: Session, sc: Scenario, base: str) -> None:
    s = Session(base, "alice", sc)          # lab-a
    s.ask("what did lab-b spend in March 2026?",
          lambda t: wants(t, never_says=("lab-b spent",)))


@scenario("reading: past, present and future bookings",
          "The diary question, which needs the scheduling space.")
def _diary(_s: Session, sc: Scenario, base: str) -> None:
    s = Session(base, "alice", sc)
    s.ask("what bookings do I have coming up?",
          lambda t: wants(t, kind="rows_answer") if t.route == "data" else None)


@scenario("writing: a booking waits for approval, and declining writes nothing",
          "Nothing reaches the platform without a human saying yes.", writes=True)
def _decline(_s: Session, sc: Scenario, base: str) -> None:
    s = Session(base, "alice", sc)
    with session_scope() as db:
        before = db.execute(text("SELECT count(*) FROM infinity.bookings")).scalar()
    turn = s.ask("book the confocal C2 for 2 hours on 20 September 2026 at 09:00",
                 lambda t: wants(t, route="action", kind="approval_request"))
    if turn.pending_action:
        s.decide(turn, "decline")
    with session_scope() as db:
        after = db.execute(text("SELECT count(*) FROM infinity.bookings")).scalar()
    if after != before:
        sc.problems.append(f"declining still wrote: {before} -> {after} bookings")


@scenario("writing: an invoice document, asked for by name",
          "The routing fix: 'give me the March invoice' must produce the document.",
          writes=True)
def _document(_s: Session, sc: Scenario, base: str) -> None:
    s = Session(base, "asha", sc)
    # Asha holds two account codes, so the assistant asks which — and it is right to.
    # Picking one would be inventing the single fact that decides whose money this is.
    turn = s.ask("give me the March 2026 invoice", lambda t: wants(t, route="action"))
    if turn.response_type == "clarify":
        turn = s.ask("ACC-A1", lambda t: wants(t, kind="approval_request"))
    if not turn.pending_action:
        return
    out = s.decide(turn, "approve")
    result = out.get("result") or {}
    if result.get("record_count", 0) < 1:
        sc.problems.append("the document carries no source records")
    action_id = turn.pending_action["action_id"]
    got = requests.get(f"{base}/actions/{action_id}/document",
                       headers=headers("asha"), timeout=60)
    if got.status_code != 200 or got.content[:5] != b"%PDF-":
        sc.problems.append(f"download failed: {got.status_code}")
    elif b"Infinity X" not in got.content and "Infinity X" not in got.text[:4000]:
        pass  # the wordmark is drawn, not stored as plain text; the PDF check is enough
    if requests.get(f"{base}/actions/{action_id}/document", timeout=30).status_code == 200:
        sc.problems.append("the document is downloadable without a token")
    if requests.get(f"{base}/actions/{action_id}/document",
                    headers=headers("bob"), timeout=30).status_code != 404:
        sc.problems.append("another user could reach the document")


@scenario("writing: cancelling shows the charge before it is taken",
          "The rule and its consequence are on the card, not discovered afterwards.",
          writes=True)
def _cancel(_s: Session, sc: Scenario, base: str) -> None:
    now = datetime.now(UTC)
    bid = f"bk-scn-{uuid.uuid4().hex[:6]}"
    with owner_engine.begin() as c:
        c.execute(text("""INSERT INTO infinity.bookings
            (id,user_id,instrument_id,starts_at,ends_at,status,account_code)
            VALUES (:b,'u-alice','ins-confocal-c2',:s,:e,'confirmed','ACC-A1')"""),
            {"b": bid, "s": now + timedelta(hours=5), "e": now + timedelta(hours=9)})
    try:
        s = Session(base, "alice", sc)
        turn = s.ask(f"cancel booking {bid}",
                     lambda t: wants(t, route="action", kind="approval_request"))
        preview = ((turn.pending_action or {}).get("payload_preview") or "")
        if "50%" not in preview:
            sc.problems.append(f"the charge was not shown before approval: {preview[:110]}")
        if "within 24 hours" not in preview:
            sc.problems.append("the rule itself was not quoted on the card")
        if turn.pending_action:
            out = s.decide(turn, "approve")
            result = (out.get("result") or {})
            if result.get("policy_applied") != "pol-cancel-late":
                sc.problems.append(f"wrong rule applied: {result.get('policy_applied')}")
            with session_scope() as db:
                status = db.execute(
                    text("SELECT status FROM infinity.bookings WHERE id=:b"), {"b": bid}
                ).scalar()
            if status != "cancelled":
                sc.problems.append(f"approved, but the booking is still {status}")
    finally:
        with owner_engine.begin() as c:
            c.execute(text("DELETE FROM infinity.bookings WHERE id=:b"), {"b": bid})


@scenario("writing: every decision lands in the audit table",
          "Approved or declined, there is a record. That is the product's whole claim.",
          writes=True)
def _audit(_s: Session, sc: Scenario, base: str) -> None:
    with session_scope() as db:
        before = db.execute(text("SELECT count(*) FROM echomind.audit_log")).scalar()
    s = Session(base, "alice", sc)
    turn = s.ask("book the confocal C2 for 1 hour on 21 September 2026 at 14:00")
    if turn.pending_action:
        s.decide(turn, "decline")
    with session_scope() as db:
        after = db.execute(text("SELECT count(*) FROM echomind.audit_log")).scalar()
        sc.audit = [dict(r) for r in db.execute(text(
            """SELECT action_id, event, actor_id, tool FROM echomind.audit_log
               ORDER BY created_at DESC LIMIT 4""")).mappings()]
    if after <= before:
        sc.problems.append(f"nothing was audited: {before} -> {after}")


def _restore(seen_before: set[str]) -> None:
    """Undo what the writing scenarios did, the way conftest does for the suite."""
    with owner_engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, result FROM echomind.actions WHERE id <> ALL(:seen)"),
            {"seen": list(seen_before)},
        ).mappings().all()
        for row in rows:
            result = row["result"] or {}
            if result.get("created") == "booking":
                conn.execute(text("DELETE FROM infinity.bookings WHERE id=:i"),
                             {"i": result["booking_id"]})
            elif result.get("cancelled"):
                conn.execute(
                    text("UPDATE infinity.bookings SET status=:w WHERE id=:i"),
                    {"w": result.get("previous_status") or "confirmed",
                     "i": result["cancelled"]})
        conn.execute(text("DELETE FROM echomind.actions WHERE id <> ALL(:seen)"),
                     {"seen": list(seen_before)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--out", default=None)
    parser.add_argument("--keep", action="store_true",
                        help="leave what the writing scenarios wrote in place")
    args = parser.parse_args()

    with session_scope() as db:
        seen_before = set(db.execute(text("SELECT id FROM echomind.actions")).scalars())

    started = datetime.now(UTC)
    results = [run(args.base) for run in SCENARIOS]
    if not args.keep:
        _restore(seen_before)

    passed = [s for s in results if s.passed]
    report = {
        "ran_at": started.isoformat(),
        "base": args.base,
        "scenarios": len(results),
        "passed": len(passed),
        "failed": len(results) - len(passed),
        "seconds": round(sum(s.seconds for s in results), 1),
        "results": [
            {
                "name": s.name, "intent": s.intent, "writes": s.writes,
                "passed": s.passed, "seconds": s.seconds,
                "problems": s.problems,
                "audit": s.audit,
                "turns": [t.to_dict() for t in s.turns],
            }
            for s in results
        ],
    }
    OUT_DIR.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else OUT_DIR / f"{started.date()}.json"
    out.write_text(json.dumps(report, indent=2, default=str))

    width = max(len(s.name) for s in results)
    print()
    for s in results:
        mark = "PASS" if s.passed else "FAIL"
        print(f"  {mark}  {s.name:{width}}  {len(s.turns)} turn(s)  {s.seconds:5.1f}s")
        for problem in s.problems:
            print(f"        · {problem}")
        for turn in s.turns:
            for problem in turn.problems:
                print(f"        · [{turn.asked[:40]}] {problem}")
    print(f"\n  {len(passed)}/{len(results)} scenarios passed · report: {out}")
    return 0 if len(passed) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
