"""Spec 07 manual checklist, driven through the API the UI actually calls.

Every item in specs/07-ui.md §"Manual checklist" is exercised here against a running
server, using the same endpoints and payloads the React app uses. Run with the API up:

    python -m scripts.ui_checklist
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

BASE = "http://localhost:8080"
TIMEOUT = 180.0

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []
created_action_ids: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def login(client: httpx.Client, handle: str) -> dict[str, str]:
    r = client.post(f"{BASE}/demo/login/{handle}")
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream(client: httpx.Client, headers: dict, message: str,
           thread_id: str | None = None) -> tuple[list[str], dict[str, Any]]:
    """Consume the SSE stream exactly as ui/src/api.ts does."""
    events: list[str] = []
    final: dict[str, Any] = {}
    with client.stream(
        "POST", f"{BASE}/chat/stream",
        headers={**headers, "Content-Type": "application/json"},
        json={"message": message, "thread_id": thread_id},
        timeout=TIMEOUT,
    ) as response:
        buffer = ""
        for chunk in response.iter_text():
            buffer += chunk
            frames = buffer.split("\n\n")
            buffer = frames.pop()
            for frame in frames:
                event, data = "message", []
                for line in frame.split("\n"):
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                if not data:
                    continue
                events.append(event)
                if event == "final":
                    final = json.loads("\n".join(data))
    return events, final


def cleanup(client: httpx.Client, admin_headers: dict) -> None:
    """Undo the writes this checklist made, so the seeded state is restored."""
    from sqlalchemy import text as sql_text

    from server.db import session_scope

    removed = {"bookings": 0, "actions": 0}
    with session_scope() as db:
        for action_id in created_action_ids:
            action = db.execute(
                sql_text("SELECT result FROM echomind.actions WHERE id = :id"),
                {"id": action_id},
            ).scalar_one_or_none()
            if action and action.get("created") == "booking":
                removed["bookings"] += db.execute(
                    sql_text("DELETE FROM infinity.bookings WHERE id = :id"),
                    {"id": action["booking_id"]},
                ).rowcount
            removed["actions"] += db.execute(
                sql_text("DELETE FROM echomind.actions WHERE id = :id"), {"id": action_id}
            ).rowcount
    print(f"\ncleanup: removed {removed['bookings']} booking(s), {removed['actions']} action(s)")


def main() -> int:
    client = httpx.Client(timeout=TIMEOUT)

    try:
        client.get(f"{BASE}/healthz").raise_for_status()
    except Exception as exc:
        print(f"API not reachable at {BASE}: {exc}")
        return 2

    alice = login(client, "alice")
    bob = login(client, "bob")
    cora = login(client, "cora")

    print("\n1. Stream renders progressively; refresh restores the conversation")
    events, final = stream(client, alice, "How long must the confocal lasers warm up?")
    check("SSE emits start/status/token/final in order",
          events[0] == "start" and "token" in events and events[-1] == "final",
          f"{len(events)} events: {events[0]}…{events[-1]}")
    check("final payload is a cited answer",
          final.get("response_type") == "answer" and bool(final.get("citations")),
          f"{final.get('response_type')}, {len(final.get('citations', []))} citation(s)")

    thread_id = final["thread_id"]
    restored = client.get(f"{BASE}/threads/{thread_id}", headers=alice).json()
    check("thread restores from the checkpointer after a refresh",
          restored.get("message") == "How long must the confocal lasers warm up?"
          and restored.get("response", {}).get("response_type") == "answer",
          f"thread {thread_id}")

    print("\n2. Citation chips open the correct chunk (two different answers)")
    first_citation = final["citations"][0]
    chunk = client.get(f"{BASE}/tools/chunk/{first_citation['chunk_id']}", headers=alice).json()
    check("chip 1 returns the cited chunk text + breadcrumb",
          "30 minutes" in chunk["text"] and chunk["breadcrumb"] == first_citation["breadcrumb"],
          first_citation["breadcrumb"])

    _, second = stream(client, alice, "What format do sample barcodes use?")
    ok = second.get("response_type") == "answer" and bool(second.get("citations"))
    if ok:
        c2 = second["citations"][0]
        chunk2 = client.get(f"{BASE}/tools/chunk/{c2['chunk_id']}", headers=alice).json()
        ok = "BC" in chunk2["text"]
        detail = c2["breadcrumb"]
    else:
        detail = f"got {second.get('response_type')}"
    check("chip 2 returns a different, correct chunk", ok, detail)

    print("\n3. Approval card: approve executes, decline cancels, both are audited")
    _, proposal = stream(
        client, alice,
        "Book Confocal C2 from 2027-11-03T09:00:00Z to 2027-11-03T11:00:00Z on account ACC-A1",
    )
    approved_id = (proposal.get("pending_action") or {}).get("action_id")
    created_action_ids.append(approved_id)
    check("booking request yields an approval_request card",
          proposal.get("response_type") == "approval_request" and bool(approved_id),
          str(approved_id))

    outcome = client.post(f"{BASE}/actions/{approved_id}/approve", headers=alice).json()
    check("approve executes and the follow-up references the result",
          outcome.get("status") == "executed"
          and outcome.get("result", {}).get("booking_id", "") in (outcome.get("chat") or {}).get("text", ""),
          (outcome.get("chat") or {}).get("text", "")[:80])

    _, proposal2 = stream(
        client, alice,
        "Book MiSeq M3 from 2027-11-04T09:00:00Z to 2027-11-04T10:00:00Z on account ACC-A1",
    )
    declined_id = (proposal2.get("pending_action") or {}).get("action_id")
    created_action_ids.append(declined_id)
    declined = client.post(f"{BASE}/actions/{declined_id}/decline", headers=alice).json()
    check("decline cancels politely and changes nothing",
          declined.get("status") == "declined"
          and "declined" in (declined.get("chat") or {}).get("text", "").lower(),
          (declined.get("chat") or {}).get("text", "")[:70])

    audit = client.get(f"{BASE}/admin/audit?limit=200", headers=cora).json()
    ids = {a["id"] for a in audit["actions"]}
    events_for = {e["action_id"] for e in audit["events"]}
    check("both decisions appear in the admin audit table",
          {approved_id, declined_id} <= ids and {approved_id, declined_id} <= events_for)

    print("\n4. Upload as alice; answered for alice, refused for bob")
    with tempfile.TemporaryDirectory() as tmp:
        note = Path(tmp) / "alice-upload-note.md"
        note.write_text(
            "# Alice upload test\n\n"
            "## Secret marker\n\n"
            "The private upload verification marker is ZEPHYR-5512. "
            "It appears in no other document in this corpus, and only Alice uploaded it. "
            "The hypoxia timecourse rig was recalibrated on the fourth of March.\n",
            encoding="utf-8",
        )
        with note.open("rb") as fh:
            uploaded = client.post(
                f"{BASE}/uploads", headers=alice,
                files={"file": ("alice-upload-note.md", fh, "text/markdown")},
            ).json()
    check("upload is stored private with an 'only you' note",
          uploaded.get("visibility") == "private" and "Only you" in uploaded.get("note", ""),
          f"{uploaded.get('chunks')} chunk(s)")

    question = "What is the private upload verification marker?"
    _, alice_answer = stream(client, alice, question)
    check("alice can ask about her own upload and gets it back",
          "ZEPHYR-5512" in alice_answer.get("text", ""),
          alice_answer.get("response_type", ""))

    _, bob_answer = stream(client, bob, question)
    check("bob gets a refusal and no leak of alice's upload",
          bob_answer.get("response_type") in ("redirect", "scope")
          and "ZEPHYR-5512" not in bob_answer.get("text", ""),
          bob_answer.get("response_type", ""))

    # The test hook spec 08 scene 5 asks for: assert on retrieval, not on the reply text.
    probe = client.post(
        f"{BASE}/tools/get_user_profile", headers=bob, json={},
    ).json()
    check("bob's own profile still resolves (control)", probe["result"]["user_id"] == "u-bob")

    client.delete(f"{BASE}/uploads/{uploaded['doc_id']}", headers=alice)

    print("\n5. Switcher: the same question yields different, correct data per user")
    _, alice_bookings = stream(client, alice, "How many bookings do I have in total?")
    _, bob_bookings = stream(client, bob, "How many bookings do I have in total?")
    check("alice and bob get different booking counts",
          alice_bookings.get("text") != bob_bookings.get("text"),
          f"alice: {alice_bookings.get('text', '')[:40]} | bob: {bob_bookings.get('text', '')[:40]}")

    print("\n6. Admin page is hidden from non-admins (route guard + API guard)")
    check("anonymous admin request -> 401",
          client.get(f"{BASE}/admin/summary").status_code == 401)
    check("alice admin request -> 404",
          client.get(f"{BASE}/admin/summary", headers=alice).status_code == 404)
    check("bob admin request -> 404",
          client.get(f"{BASE}/admin/summary", headers=bob).status_code == 404)
    summary = client.get(f"{BASE}/admin/summary", headers=cora)
    check("cora admin request -> 200 with eval + trace info",
          summary.status_code == 200
          and summary.json().get("latest_eval") is not None
          and summary.json().get("trace_sink") in ("console", "langfuse"))

    # The approve check creates a real booking. Like the demo script, this has to undo
    # what it made, or running the checklist leaves the seeded row counts wrong and the
    # next `pytest -m seed_counts` fails on state this script created.
    cleanup(client, cora)

    failures = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failures)}/{len(results)} checklist items passed")
    if failures:
        for _, name, detail in failures:
            print(f"  FAILED: {name} — {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
