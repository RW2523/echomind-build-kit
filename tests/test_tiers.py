"""M2 verification — permission tiers (pytest -m tiers).

Covers the required tests in spec 05 §"Required tests" other than the chat-level one,
which lands with the agent in M5 (tests/test_agent.py).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.db import session_scope
from server.mcp import actions as actions_mod
from server.mcp import tools as T
from server.mcp.errors import ToolError

pytestmark = pytest.mark.tiers


@pytest.fixture(scope="module")
def client():
    from server.main import app

    with TestClient(app) as c:
        yield c


def _err(fn, *args, **kwargs) -> dict:
    with pytest.raises(ToolError) as exc:
        fn(*args, **kwargs)
    return exc.value.to_dict()["error"]


# --- bob is denied alice's data, with no existence leak ------------------------------


def test_bob_cannot_read_alices_profile(ctxs):
    assert _err(T.get_user_profile, ctxs["bob"], user_id="u-alice")["code"] == "forbidden"


def test_forbidden_is_indistinguishable_from_nonexistent(ctxs):
    """The error must not work as an existence oracle."""
    real = _err(T.get_user_profile, ctxs["bob"], user_id="u-alice")
    fake = _err(T.get_user_profile, ctxs["bob"], user_id="u-does-not-exist-9999")
    assert real == fake


def test_bob_cannot_read_alices_usage(ctxs):
    assert _err(T.get_usage_records, ctxs["bob"], scope="user", id="u-alice")["code"] == "forbidden"


def test_bob_cannot_read_lab_a_usage(ctxs):
    assert _err(T.get_usage_records, ctxs["bob"], scope="lab", id="lab-a")["code"] == "forbidden"


def test_bob_cannot_read_alices_account_billing(ctxs):
    assert _err(T.get_billing_summary, ctxs["bob"],
                account_code="ACC-A1", period="2026-03")["code"] == "forbidden"


def test_bob_cannot_track_alices_sample(ctxs):
    with session_scope() as s:
        barcode = s.execute(
            text(
                """SELECT s.barcode FROM infinity.samples s
                   JOIN infinity.service_requests sr ON sr.id = s.request_id
                   WHERE sr.user_id = 'u-alice' LIMIT 1"""
            )
        ).scalar_one()
    assert _err(T.track_sample, ctxs["bob"], barcode=barcode)["code"] == "forbidden"


def test_bob_cannot_read_alices_request(ctxs):
    with session_scope() as s:
        rid = s.execute(
            text("SELECT id FROM infinity.service_requests WHERE user_id = 'u-alice' LIMIT 1")
        ).scalar_one()
    assert _err(T.get_request_status, ctxs["bob"], request_id=rid)["code"] == "forbidden"


def test_plain_user_cannot_run_sql(ctxs):
    assert _err(T.run_readonly_sql, ctxs["bob"], sql="SELECT * FROM v_bookings")["code"] == "forbidden"


def test_plain_user_cannot_read_projects(ctxs):
    assert _err(T.get_project_overview, ctxs["bob"],
                project_id="prj-neuro-atlas")["code"] == "forbidden"


def test_plain_user_cannot_generate_admin_document(ctxs):
    assert _err(T.generate_document, ctxs["bob"], template="monthly_summary")["code"] == "forbidden"


# --- asha (PI, lab A) sees lab A only -----------------------------------------------


def test_asha_reads_lab_a_usage(ctxs):
    out = T.get_usage_records(ctxs["asha"], scope="lab", id="lab-a")
    assert out["rows"]
    assert {r["lab_id"] for r in out["rows"]} == {"lab-a"}


def test_asha_is_forbidden_lab_b_usage(ctxs):
    assert _err(T.get_usage_records, ctxs["asha"], scope="lab", id="lab-b")["code"] == "forbidden"


def test_asha_can_read_a_lab_a_members_profile(ctxs):
    assert T.get_user_profile(ctxs["asha"], user_id="u-alice")["user_id"] == "u-alice"


def test_asha_cannot_read_a_lab_b_members_profile(ctxs):
    assert _err(T.get_user_profile, ctxs["asha"], user_id="u-bob")["code"] == "forbidden"


def test_asha_sql_is_lab_scoped_even_with_no_filter_written(ctxs):
    """The required test: she writes no lab predicate and still sees only lab A."""
    out = T.run_readonly_sql(
        ctxs["asha"], sql="SELECT DISTINCT lab_id FROM v_billing_lines"
    )
    assert out["lab_filtered"] is True
    assert [r["lab_id"] for r in out["rows"]] == ["lab-a"]


def test_asha_cannot_escape_the_rewrite_with_her_own_predicate(ctxs):
    out = T.run_readonly_sql(
        ctxs["asha"], sql="SELECT * FROM v_billing_lines WHERE lab_id = 'lab-b'"
    )
    assert out["row_count"] == 0


def test_asha_cannot_escape_the_rewrite_via_a_join(ctxs):
    out = T.run_readonly_sql(
        ctxs["asha"],
        sql="SELECT DISTINCT b.lab_id FROM v_bookings b JOIN v_billing_lines l "
            "ON l.lab_id = b.lab_id",
    )
    assert {r["lab_id"] for r in out["rows"]} <= {"lab-a"}


def test_asha_march_confocal_total_is_visible_to_her(ctxs):
    out = T.run_readonly_sql(
        ctxs["asha"],
        sql="SELECT sum(amount) AS total FROM v_billing_lines "
            "WHERE period = '2026-03' AND instrument = 'Confocal C2'",
    )
    assert float(out["rows"][0]["total"]) == 412.00


# --- cora (admin) sees all ------------------------------------------------------------


def test_cora_reads_any_profile(ctxs):
    for uid in ("u-alice", "u-bob", "u-asha"):
        assert T.get_user_profile(ctxs["cora"], user_id=uid)["user_id"] == uid


def test_cora_sees_every_lab_in_sql(ctxs):
    out = T.run_readonly_sql(ctxs["cora"], sql="SELECT DISTINCT lab_id FROM v_billing_lines")
    assert len({r["lab_id"] for r in out["rows"]}) > 1
    assert out["lab_filtered"] is False


def test_cora_gets_not_found_for_a_genuinely_missing_user(ctxs):
    """An admin may see everything, so a missing row is not a leak — it is the truth."""
    assert _err(T.get_user_profile, ctxs["cora"], user_id="u-nope")["code"] == "not_found"


# --- approval authority ---------------------------------------------------------------


def test_alice_cannot_approve_bobs_action_but_cora_can(ctxs):
    pending = T.request_booking(
        ctxs["bob"], instrument_id="ins-miseq",
        starts_at="2027-04-01T09:00:00Z", ends_at="2027-04-01T11:00:00Z",
        account_code="ACC-B1",
    )
    action_id = pending["action_id"]

    assert _err(actions_mod.approve, ctxs["alice"], action_id)["code"] == "forbidden"
    assert actions_mod.get_action(action_id)["status"] == "pending"

    out = actions_mod.approve(ctxs["cora"], action_id)
    assert out["status"] == "executed"
    assert actions_mod.get_action(action_id)["approver_id"] == "u-cora"


def test_a_user_may_approve_their_own_action(ctxs):
    pending = T.generate_document(ctxs["alice"], template="usage_report",
                                  params={"month": "2026-03"})
    out = actions_mod.approve(ctxs["alice"], pending["action_id"])
    assert out["status"] == "executed"


def test_alice_cannot_decline_bobs_action(ctxs):
    pending = T.generate_document(ctxs["bob"], template="usage_report")
    assert _err(actions_mod.decline, ctxs["alice"], pending["action_id"])["code"] == "forbidden"
    actions_mod.decline(ctxs["bob"], pending["action_id"])


# --- the dependency itself --------------------------------------------------------------


def test_missing_token_is_rejected(client):
    assert client.get("/tools").status_code == 401


def test_tampered_signature_is_rejected(client, tokens):
    good = tokens["alice"]
    head, payload, sig = good.split(".")
    tampered = f"{head}.{payload}.{'A' * len(sig)}"
    r = client.get("/tools", headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code == 401


def test_tampered_payload_is_rejected(client, tokens):
    """Re-encoding the claims as admin without the key must not work."""
    import base64
    import json

    head, payload, sig = tokens["bob"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["role"] = "admin"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    r = client.get("/tools", headers={"Authorization": f"Bearer {head}.{forged}.{sig}"})
    assert r.status_code == 401


def test_valid_token_reaches_the_tool_list(client, tokens):
    r = client.get("/tools", headers={"Authorization": f"Bearer {tokens['alice']}"})
    assert r.status_code == 200
    assert len(r.json()["tools"]) == 15


def test_forbidden_over_http_keeps_the_error_shape(client, tokens):
    r = client.post(
        "/tools/get_user_profile",
        headers={"Authorization": f"Bearer {tokens['bob']}"},
        json={"user_id": "u-alice"},
    )
    assert r.status_code == 403
    assert set(r.json()["detail"]) == {"code", "message", "hint"}
    assert r.json()["detail"]["code"] == "forbidden"


def test_bob_cannot_fetch_alices_action_over_http(client, tokens, ctxs):
    pending = T.generate_document(ctxs["alice"], template="usage_report")
    r = client.get(
        f"/actions/{pending['action_id']}",
        headers={"Authorization": f"Bearer {tokens['bob']}"},
    )
    assert r.status_code == 404
    actions_mod.decline(ctxs["alice"], pending["action_id"])
