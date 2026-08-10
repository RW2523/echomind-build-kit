"""The six-scene demo — `make demo`.

Every scene is driven through the real HTTP API, exactly as the UI drives it, and
asserts machine-checkable outcomes. Prints PASS/FAIL per scene and exits non-zero if any
scene fails.

The script starts the API itself if one is not already listening, so `make demo` works
from a cold shell. It cleans up everything it creates, so it is green twice in a row —
which is the actual bar spec 08 sets.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from server.config import REPO_ROOT, settings
from server.db import owner_session, session_scope

BASE = f"http://localhost:{settings.api_port}"
FIXTURES = REPO_ROOT / "scripts" / "fixtures"
TIMEOUT = 240.0

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


@dataclass
class Scene:
    number: int
    title: str
    checks: list[tuple[bool, str]] = field(default_factory=list)
    error: str | None = None

    def check(self, ok: bool, description: str) -> bool:
        self.checks.append((bool(ok), description))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return self.error is None and bool(self.checks) and all(ok for ok, _ in self.checks)


class Demo:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=TIMEOUT)
        self.scenes: list[Scene] = []
        self.created_users: list[str] = []
        self.created_bookings: list[str] = []
        self.created_requests: list[str] = []
        self.created_docs: list[tuple[str, dict]] = []
        self.created_actions: list[str] = []

    # --- plumbing ---------------------------------------------------------------

    def token(self, handle: str) -> dict[str, str]:
        r = self.client.post(f"{BASE}/demo/login/{handle}")
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def ask(self, headers: dict, message: str, thread_id: str | None = None) -> dict[str, Any]:
        r = self.client.post(
            f"{BASE}/chat", headers=headers,
            json={"message": message, "thread_id": thread_id},
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("pending_action"):
            self.created_actions.append(payload["pending_action"]["action_id"])
        return payload

    def decide(self, headers: dict, action_id: str, choice: str) -> dict[str, Any]:
        r = self.client.post(f"{BASE}/actions/{action_id}/{choice}", headers=headers)
        r.raise_for_status()
        return r.json()

    def track(self, result: dict[str, Any]) -> None:
        created = (result or {}).get("created")
        if created == "user":
            self.created_users.append(result["user_id"])
        elif created == "booking":
            self.created_bookings.append(result["booking_id"])
        elif created == "service_request":
            self.created_requests.append(result["request_id"])

    def scene(self, number: int, title: str) -> Scene:
        s = Scene(number, title)
        self.scenes.append(s)
        print(f"\n{BOLD}Scene {number} — {title}{RESET}")
        return s

    # --- scenes -----------------------------------------------------------------

    def scene_1_onboarding(self) -> None:
        s = self.scene(1, "Onboarding — nothing happens without approval")
        asha = self.token("asha")

        first = self.ask(
            asha,
            "I need to onboard a new researcher joining our lab. Her name is Mira Solberg "
            "and her email is mira.solberg@example.edu.",
        )
        thread = first["thread_id"]
        print(f"  {DIM}> {first['text'][:110]}{RESET}")

        # The agent may already have enough (asha has exactly one lab) or may come back
        # for the PI acknowledgement — both are correct, and it must never assume consent.
        proposal = first
        if first["response_type"] != "approval_request":
            proposal = self.ask(
                asha,
                "Yes — put her in lab-a on account ACC-A1. As the PI of Lab A I acknowledge "
                "this onboarding.",
                thread_id=thread,
            )
            print(f"  {DIM}> {proposal['text'][:110]}{RESET}")

        action = proposal.get("pending_action") or {}
        if not s.check(proposal["response_type"] == "approval_request",
                       f"agent returns an approval_request (got {proposal['response_type']})"):
            return
        s.check(action.get("kind") == "onboarding", "the pending action is an onboarding")
        s.check(action.get("payload", {}).get("email") == "mira.solberg@example.edu",
                "the payload carries the email the PI gave")
        s.check(action.get("payload", {}).get("pi_ack") is True,
                "the PI's acknowledgement is recorded on the payload")

        with session_scope() as db:
            before = db.execute(
                text("SELECT count(*) FROM infinity.users WHERE email = 'mira.solberg@example.edu'")
            ).scalar_one()
        s.check(before == 0, "no user row exists before approval")

        outcome = self.decide(asha, action["action_id"], "approve")
        self.track(outcome.get("result", {}))
        s.check(outcome["status"] == "executed", "approving as the requester executes it")

        with session_scope() as db:
            row = db.execute(
                text(
                    "SELECT id, role, lab_id, training FROM infinity.users "
                    "WHERE email = 'mira.solberg@example.edu'"
                )
            ).mappings().first()
            audit = [
                e[0] for e in db.execute(
                    text("SELECT event FROM echomind.audit_log WHERE action_id = :a ORDER BY id"),
                    {"a": action["action_id"]},
                ).all()
            ]
        s.check(row is not None, "a new user row now exists")
        s.check(bool(row) and row["lab_id"] == "lab-a", "the new user is in lab A")
        s.check(bool(row) and row["training"] == {},
                "the new user has no training yet — access is pending")
        s.check(audit == ["proposed", "approved", "executed"],
                f"audit shows the whole story: {audit}")

    def scene_2_availability_and_booking(self) -> None:
        s = self.scene(2, "Availability, then a booking that waits for you")
        alice = self.token("alice")

        availability = self.ask(
            alice,
            "Is Confocal C2 free on Thursday 2027-12-02 between 14:00 and 16:00 UTC?",
        )
        thread = availability["thread_id"]
        print(f"  {DIM}> {availability['text'][:110]}{RESET}")
        s.check(availability["response_type"] == "rows_answer",
                f"availability is answered from records (got {availability['response_type']})")
        s.check(bool(availability["rows"]), "the answer carries the rows it came from")

        proposal = self.ask(alice, "Great — book it on account ACC-A1.", thread_id=thread)
        print(f"  {DIM}> {proposal['text'][:110]}{RESET}")
        action = proposal.get("pending_action") or {}
        if not s.check(proposal["response_type"] == "approval_request",
                       f"the follow-up produces an approval_request (got {proposal['response_type']})"):
            return
        s.check(action.get("kind") == "booking", "the pending action is a booking")
        s.check(action.get("payload", {}).get("instrument_id") == "ins-confocal-c2",
                "'it' resolved to Confocal C2 from the previous turn")

        with session_scope() as db:
            before = db.execute(
                text("SELECT count(*) FROM infinity.bookings "
                     "WHERE user_id = 'u-alice' AND starts_at = '2027-12-02T14:00:00Z'")
            ).scalar_one()
        s.check(before == 0, "no booking row exists before approval")

        outcome = self.decide(alice, action["action_id"], "approve")
        self.track(outcome.get("result", {}))
        s.check(outcome["status"] == "executed", "approval executes the booking")

        with session_scope() as db:
            row = db.execute(
                text("SELECT id, status, account_code FROM infinity.bookings "
                     "WHERE user_id = 'u-alice' AND starts_at = '2027-12-02T14:00:00Z'")
            ).mappings().first()
            audit = [
                e[0] for e in db.execute(
                    text("SELECT event FROM echomind.audit_log WHERE action_id = :a ORDER BY id"),
                    {"a": action["action_id"]},
                ).all()
            ]
        s.check(row is not None, "the booking row exists")
        s.check(bool(row) and row["status"] == "requested",
                f"the booking is 'requested' (got {row['status'] if row else None})")
        s.check(bool(row) and row["account_code"] == "ACC-A1", "charged to the account given")
        s.check("proposed" in audit and "approved" in audit and "executed" in audit,
                f"both audit entries are present: {audit}")

    def scene_3_billing_truth(self) -> None:
        s = self.scene(3, "Billing truth — the number comes from the ledger")
        asha = self.token("asha")

        answer = self.ask(asha, "Why was lab A charged $412 in March?")
        print(f"  {DIM}> {answer['text'][:140]}{RESET}")

        s.check(answer["response_type"] == "rows_answer",
                f"answered from records (got {answer['response_type']})")
        s.check("412.00" in answer["text"], "the reply states 412.00 exactly")

        from server.agent.data import verify_numbers

        offenders = verify_numbers(
            answer["text"], answer["rows"], "Why was lab A charged $412 in March?"
        )
        s.check(offenders == [],
                f"every number in the reply exists in the returned rows (stray: {offenders})")

        with session_scope() as db:
            seeded = db.execute(
                text("SELECT sum(amount) FROM reporting.v_billing_lines "
                     "WHERE lab_id = 'lab-a' AND period = '2026-03' AND instrument = 'Confocal C2'")
            ).scalar_one()
        s.check(float(seeded) == 412.00, "the seeded ledger really does say 412.00")
        s.check(all("lab-b" not in json.dumps(r, default=str) for r in answer["rows"]),
                "a PI sees only their own lab's lines")

    def scene_4_document_to_service_request(self) -> None:
        s = self.scene(4, "A filled form becomes a service request")
        alice = self.token("alice")

        form = FIXTURES / "rna-seq-submission-form.pdf"
        with form.open("rb") as fh:
            uploaded = self.client.post(
                f"{BASE}/uploads", headers=alice,
                files={"file": (form.name, fh, "application/pdf")},
            ).json()
        self.created_docs.append((uploaded["doc_id"], alice))
        s.check(uploaded.get("visibility") == "private", "the form is uploaded to alice's own space")

        proposal = self.ask(
            alice,
            "I've uploaded my filled bulk RNA-seq submission form. Please submit it as a "
            "service request using the values on the form.",
        )
        print(f"  {DIM}> {proposal['text'][:130]}{RESET}")
        action = proposal.get("pending_action") or {}
        if not s.check(proposal["response_type"] == "approval_request",
                       f"agent drafts a service request (got {proposal['response_type']})"):
            return

        fields = action.get("payload", {}).get("fields", {}) or {}
        s.check(action.get("payload", {}).get("template_id") == "tpl-rna-seq",
                f"it picked the RNA-seq template (got {action.get('payload', {}).get('template_id')})")
        s.check(str(fields.get("sample_count")) == "12",
                f"sample_count extracted from the form: {fields.get('sample_count')}")
        s.check(str(fields.get("organism", "")).lower() == "mus musculus",
                f"organism extracted from the form: {fields.get('organism')}")
        s.check(str(fields.get("read_length")) == "150bp",
                f"read_length extracted from the form: {fields.get('read_length')}")

        outcome = self.decide(alice, action["action_id"], "approve")
        self.track(outcome.get("result", {}))
        s.check(outcome["status"] == "executed", "approval creates the request")

        request_id = outcome.get("result", {}).get("request_id")
        with session_scope() as db:
            row = db.execute(
                text("SELECT user_id, template_id, fields, status FROM infinity.service_requests "
                     "WHERE id = :id"),
                {"id": request_id},
            ).mappings().first()
        s.check(row is not None, "the service_requests row exists")
        s.check(bool(row) and row["template_id"] == "tpl-rna-seq", "against the right template")
        s.check(bool(row) and str(row["fields"].get("sample_count")) == "12",
                "the stored row matches the extracted fields")

    def scene_5_per_user_rag(self) -> None:
        s = self.scene(5, "Per-user knowledge — bob simply cannot see it")
        alice, bob = self.token("alice"), self.token("bob")

        note = FIXTURES / "private-note.md"
        with note.open("rb") as fh:
            uploaded = self.client.post(
                f"{BASE}/uploads", headers=alice,
                files={"file": (note.name, fh, "text/markdown")},
            ).json()
        self.created_docs.append((uploaded["doc_id"], alice))
        doc_id = uploaded["doc_id"]
        s.check("Only you" in uploaded.get("note", ""), "the upload is marked private to alice")

        question = "What is the private marker in my hypoxia timecourse note?"
        alice_answer = self.ask(alice, question)
        print(f"  {DIM}> alice: {alice_answer['text'][:110]}{RESET}")
        s.check(alice_answer["response_type"] == "answer", "alice gets an answer")
        s.check("ORRERY-3187" in alice_answer["text"], "and it contains her marker")
        s.check(bool(alice_answer["citations"]), "with a citation")

        bob_answer = self.ask(bob, question)
        print(f"  {DIM}> bob:   {bob_answer['text'][:110]}{RESET}")
        s.check(bob_answer["response_type"] in ("redirect", "scope"),
                f"bob gets a redirect (got {bob_answer['response_type']})")
        s.check("ORRERY-3187" not in bob_answer["text"], "bob's reply does not contain the marker")

        # The test hook spec 08 asks for: assert on retrieval itself, not the reply text.
        from server.auth import Ctx
        from server.rag.retrieval import retrieve

        bob_ctx = Ctx(user_id="u-bob", name="Bob", role="user", lab_ids=("lab-b",))
        retrieved = retrieve(question, bob_ctx, k=8)
        from_alice = [c for c in retrieved if c.doc_id == doc_id]
        s.check(from_alice == [],
                f"bob's retrieval returned zero chunks from alice's doc ({len(retrieved)} chunks seen)")
        s.check(all(c.visibility == "public" for c in retrieved),
                "everything bob retrieved is public")

    def scene_6_verified_or_silent(self) -> None:
        s = self.scene(6, "Verified or silent, and the paper trail")
        alice = self.token("alice")

        answer = self.ask(
            alice, "What is the facility's parking permit policy for visiting researchers?"
        )
        print(f"  {DIM}> {answer['text'][:150]}{RESET}")
        s.check(answer["response_type"] == "redirect",
                f"out-of-corpus question is redirected (got {answer['response_type']})")
        s.check(not answer["citations"], "no citations are fabricated")
        s.check("parking" not in answer["text"].lower(), "no parking policy is invented")

        gate = answer.get("gate") or {}
        named = bool(gate.get("closest_breadcrumb")) or "ask" in answer["text"].lower()
        s.check(named, "the redirect names the closest document or the right person to ask")

        traces = self.client.get(f"{BASE}/admin/traces?limit=50", headers=self.token("cora")).json()
        turn_spans = [x for x in traces["spans"] if x.get("name") == "chat.turn"]
        s.check(bool(turn_spans), f"the turn was traced ({traces['sink']}): {len(turn_spans)} turns")
        s.check(any(x.get("gate_result") for x in turn_spans),
                "the trace records the gate result")

        with session_scope() as db:
            latest = db.execute(
                text("SELECT metrics FROM echomind.eval_runs ORDER BY ran_at DESC LIMIT 1")
            ).scalar_one_or_none()
        s.check(latest is not None, "an eval_runs row exists from `make eval`")
        if latest:
            s.check(latest.get("data_exact_match") == 1.0,
                    f"the last eval had 100% data exact-match (got {latest.get('data_exact_match')})")
            s.check(latest.get("redirect_forbidden") == 1.0,
                    f"the last eval refused 100% of what it should (got {latest.get('redirect_forbidden')})")

    # --- lifecycle ---------------------------------------------------------------

    def cleanup(self) -> None:
        """Undo everything this run created, so the next run starts from the seed.

        Runs as the owner, not as the application. Removing platform rows is scaffolding:
        echomind_app may create a booking on approval and must never be able to delete
        one, so the tear-down cannot borrow its connection.
        """
        for doc_id, headers in self.created_docs:
            try:
                self.client.delete(f"{BASE}/uploads/{doc_id}", headers=headers)
            except Exception:  # noqa: BLE001
                pass
        with owner_session() as db:
            for booking_id in self.created_bookings:
                db.execute(text("DELETE FROM infinity.bookings WHERE id = :id"), {"id": booking_id})
            for request_id in self.created_requests:
                db.execute(text("DELETE FROM infinity.samples WHERE request_id = :id"),
                           {"id": request_id})
                db.execute(text("DELETE FROM infinity.service_requests WHERE id = :id"),
                           {"id": request_id})
            for user_id in self.created_users:
                db.execute(text("DELETE FROM infinity.users WHERE id = :id"), {"id": user_id})
            for action_id in self.created_actions:
                db.execute(text("DELETE FROM echomind.actions WHERE id = :id"), {"id": action_id})

    def report(self) -> int:
        print(f"\n{BOLD}{'=' * 66}{RESET}")
        for s in self.scenes:
            mark = f"{GREEN}PASS{RESET}" if s.passed else f"{RED}FAIL{RESET}"
            print(f"{mark}  Scene {s.number} — {s.title}")
            for ok, description in s.checks:
                if not ok:
                    print(f"        {RED}× {description}{RESET}")
            if s.error:
                print(f"        {RED}× errored: {s.error}{RESET}")
        passed = sum(1 for s in self.scenes if s.passed)
        total = len(self.scenes)
        colour = GREEN if passed == total else RED
        print(f"{BOLD}{'=' * 66}{RESET}")
        print(f"{colour}{BOLD}{passed}/{total} scenes passed{RESET}")
        return 0 if passed == total else 1

    def run(self) -> int:
        for method in (
            self.scene_1_onboarding,
            self.scene_2_availability_and_booking,
            self.scene_3_billing_truth,
            self.scene_4_document_to_service_request,
            self.scene_5_per_user_rag,
            self.scene_6_verified_or_silent,
        ):
            try:
                method()
            except Exception as exc:  # noqa: BLE001 — a broken scene fails, it does not abort
                if self.scenes:
                    self.scenes[-1].error = f"{type(exc).__name__}: {exc}"
                print(f"  {RED}errored: {type(exc).__name__}: {exc}{RESET}")
        return self.report()


# --- server lifecycle -------------------------------------------------------------


def api_is_up() -> bool:
    try:
        return httpx.get(f"{BASE}/healthz", timeout=2.0).json().get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def start_api() -> subprocess.Popen | None:
    """Start the API if nothing is listening, so `make demo` works from a cold shell."""
    if api_is_up():
        print(f"{DIM}using the API already running on {BASE}{RESET}")
        return None
    print(f"{DIM}starting the API on {BASE} …{RESET}")
    log = (REPO_ROOT / "logs")
    log.mkdir(exist_ok=True)
    handle = (log / "demo-api.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--host", "127.0.0.1", "--port", str(settings.api_port), "--log-level", "warning"],
        cwd=REPO_ROOT, stdout=handle, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid if hasattr(os, "setsid") else None,
    )
    for _ in range(120):
        if api_is_up():
            return process
        if process.poll() is not None:
            raise SystemExit(
                "the API exited during startup; see logs/demo-api.log"
            )
        time.sleep(0.5)
    raise SystemExit("the API did not become healthy in 60s; see logs/demo-api.log")


def stop_api(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001
        process.terminate()
    try:
        process.wait(timeout=10)
    except Exception:  # noqa: BLE001
        process.kill()


def main() -> int:
    print(f"{BOLD}EchoMind — six-scene demo{RESET}")
    print(f"{DIM}model {settings.llm_model} · embeddings {settings.embed_model} · "
          f"reranker {settings.reranker} · escalation "
          f"{'on' if settings.escalation_enabled else 'off'}{RESET}")

    process = start_api()
    demo = Demo()
    try:
        return demo.run()
    finally:
        demo.cleanup()
        stop_api(process)


if __name__ == "__main__":
    sys.exit(main())
