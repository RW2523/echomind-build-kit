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


# --- the entitlement check cannot be routed around by plan shape ---------------------


def test_nested_subject_user_id_is_lifted_so_the_check_still_runs(ctxs):
    """Regression: `subject_user_id` buried inside `arguments` skipped the check.

    The entitlement check reads the key from the plan's top level; dispatch passes
    `arguments` to the tool. A plan with the key nested therefore ran neither — no
    denial, and an unexpected kwarg splatted into the handler. Nothing leaked only
    because no tool happens to have a parameter of that name, which is luck, not a
    control. Normalising means the check sees the subject wherever the model put it.
    """
    from server.agent.data import _assert_may_read_subject, _normalise_plan

    nested = {"mode": "tool", "tool": "get_my_bookings",
              "arguments": {"subject_user_id": "u-alice"}}
    plan = _normalise_plan(nested)

    assert plan["subject_user_id"] == "u-alice", "must be lifted to the top level"
    assert "subject_user_id" not in plan["arguments"], "must not also reach the tool"

    with pytest.raises(ToolError) as excinfo:
        _assert_may_read_subject(plan, ctxs["bob"])
    assert excinfo.value.code == "forbidden"


def test_normalising_leaves_a_well_formed_plan_alone(ctxs):
    from server.agent.data import _normalise_plan

    good = {"mode": "tool", "tool": "get_my_bookings",
            "arguments": {"date_from": "2026-01-01"}, "subject_user_id": "u-alice"}
    assert _normalise_plan(dict(good)) == good


def test_the_caller_asking_about_themselves_is_not_denied(ctxs):
    """The guard must not turn "my bookings" into a refusal."""
    from server.agent.data import _assert_may_read_subject

    plan = {"mode": "tool", "tool": "get_my_bookings", "arguments": {},
            "subject_user_id": "u-bob"}
    _assert_may_read_subject(plan, ctxs["bob"])  # must not raise


# --- demo login is a deliberate switch, not a side effect of the secret ---------------


def _enabled_with(monkeypatch, *, secret: str, flag: bool) -> bool:
    """Call the real guard with a substituted settings object."""
    from server.api import demo_login
    from server.config import Settings

    monkeypatch.setattr(
        demo_login, "settings", Settings(jwt_secret=secret, demo_login_enabled=flag)
    )
    return demo_login.enabled()


def test_demo_login_is_off_with_a_real_secret_and_no_flag(monkeypatch):
    """The default for anything that is not a dev checkout."""
    assert not _enabled_with(monkeypatch, secret="x" * 48, flag=False)


def test_demo_login_is_on_for_a_dev_checkout(monkeypatch):
    """Unchanged behaviour: leave JWT_SECRET at the default and the door is open."""
    from server.api.demo_login import DEV_SECRET

    assert _enabled_with(monkeypatch, secret=DEV_SECRET, flag=False)


def test_demo_login_can_be_opened_deliberately_with_a_strong_secret(monkeypatch):
    """Regression: the two were coupled, so a publicly shared demo could only have an
    open front door by also keeping the secret printed in .env.example — which is in a
    public repository, and would have let anyone forge an admin token."""
    assert _enabled_with(monkeypatch, secret="s" * 48, flag=True)


# --- memory holds preferences, never facts --------------------------------------------


def test_memory_is_learned_from_approval_and_prefills_the_next_proposal(ctxs):
    """The whole feature: approve a booking on a code, and the next one is pre-filled.

    Pre-filled, not acted on — the value lands in a proposal the user still approves,
    and it is visible on the card, so a wrong guess costs one click.
    """
    from sqlalchemy import text as sql_text

    from server.agent import memory
    from server.db import owner_session

    alice = ctxs["alice"]
    memory.forget(alice.user_id)
    created = []
    try:
        first = T.request_booking(
            alice, instrument_id="ins-confocal-c2",
            starts_at="2027-06-01T09:00:00Z", ends_at="2027-06-01T10:00:00Z",
            account_code="ACC-A1",
        )
        created.append(first["action_id"])
        assert actions_mod.approve(alice, first["action_id"])["status"] == "executed"
        assert memory.recall(alice.user_id)[memory.ACCOUNT_CODE] == "ACC-A1"

        second = T.request_booking(
            alice, instrument_id="ins-confocal-c2",
            starts_at="2027-06-02T09:00:00Z", ends_at="2027-06-02T10:00:00Z",
        )
        created.append(second["action_id"])
        assert second["payload"]["account_code"] == "ACC-A1"
        assert "ACC-A1" in second["payload_preview"], "the approver must see the guess"
    finally:
        with owner_session() as s:
            for action_id in created:
                s.execute(sql_text("DELETE FROM echomind.actions WHERE id = :i"), {"i": action_id})
            s.execute(sql_text(
                "DELETE FROM infinity.bookings WHERE starts_at IN "
                "('2027-06-01T09:00:00Z','2027-06-02T09:00:00Z')"))
        memory.forget(alice.user_id)


def test_without_memory_the_account_code_is_still_required(ctxs):
    """Nothing is invented: no remembered code means the caller must say which."""
    from server.agent import memory

    memory.forget(ctxs["bob"].user_id)
    with pytest.raises(ToolError) as excinfo:
        T.request_booking(
            ctxs["bob"], instrument_id="ins-miseq",
            starts_at="2027-06-03T09:00:00Z", ends_at="2027-06-03T10:00:00Z",
        )
    assert excinfo.value.code == "invalid_params"


def test_memory_only_stores_the_closed_set_of_keys():
    """An open key set is how "preferences" drifts into being a cache of stale facts."""
    from server.agent import memory

    memory.forget("u-probe-memory")
    memory.remember("u-probe-memory", "favourite_number", "42")
    assert memory.recall("u-probe-memory") == {}


def test_changing_a_preference_resets_its_confirmation_count():
    """Someone who moves to a new account code has not confirmed the old one twice."""
    from sqlalchemy import text as sql_text

    from server.agent import memory
    from server.db import owner_session

    user = "u-probe-memory"
    memory.forget(user)
    try:
        memory.remember(user, memory.ACCOUNT_CODE, "ACC-A1")
        memory.remember(user, memory.ACCOUNT_CODE, "ACC-A1")
        with owner_session() as s:
            hits = s.execute(
                sql_text("SELECT hits FROM echomind.user_memory WHERE user_id=:u AND key=:k"),
                {"u": user, "k": memory.ACCOUNT_CODE},
            ).scalar_one()
        assert hits == 2

        memory.remember(user, memory.ACCOUNT_CODE, "ACC-B1")
        with owner_session() as s:
            row = s.execute(
                sql_text("SELECT value, hits FROM echomind.user_memory "
                         "WHERE user_id=:u AND key=:k"),
                {"u": user, "k": memory.ACCOUNT_CODE},
            ).one()
        assert row.value == "ACC-B1" and row.hits == 1
    finally:
        memory.forget(user)


def test_a_user_reads_and_clears_only_their_own_memory(client, tokens):
    """The key is the verified caller id, never a parameter."""
    body = client.get("/me/memory", headers={"Authorization": f"Bearer {tokens['alice']}"}).json()
    assert body["user_id"] == "u-alice"
    assert client.get("/me/memory").status_code == 401


# --- the SSO seam: institutional claims -> the context permissions read ---------------


def test_azure_ad_group_claims_map_to_a_role_and_labs():
    from server.sso import ctx_from_idp_claims

    ctx = ctx_from_idp_claims({
        "sub": "u-asha", "name": "Asha Patel",
        "groups": ["lab-lab-a", "principal-investigators", "some-unrelated-group"],
    })
    assert ctx.role == "pi"
    assert ctx.lab_ids == ("lab-a",)
    assert ctx.is_pi and not ctx.is_admin


def test_shibboleth_sends_a_semicolon_string_not_a_list():
    """Iterating that string character by character silently grants nothing at all."""
    from server.sso import ClaimMapping, ctx_from_idp_claims

    mapping = ClaimMapping(user_id="eduPersonPrincipalName", groups="isMemberOf")
    ctx = ctx_from_idp_claims(
        {
            "eduPersonPrincipalName": "asha@uni.example",
            "isMemberOf": "lab-lab-a;principal-investigators",
        },
        mapping=mapping,
    )
    assert ctx.user_id == "asha@uni.example"
    assert ctx.role == "pi"
    assert ctx.lab_ids == ("lab-a",)


def test_the_most_privileged_group_wins():
    """Someone in both groups is an admin, not whichever the dictionary yielded first."""
    from server.sso import ctx_from_idp_claims

    ctx = ctx_from_idp_claims(
        {"sub": "u-cora", "groups": ["principal-investigators", "facility-admins"]}
    )
    assert ctx.role == "admin"


def test_group_membership_is_matched_case_insensitively():
    """Active Directory is not consistent about case."""
    from server.sso import ctx_from_idp_claims

    ctx = ctx_from_idp_claims({"sub": "u-cora", "groups": ["Facility-Admins"]})
    assert ctx.role == "admin"


def test_an_unmapped_user_gets_the_least_privilege():
    from server.sso import ctx_from_idp_claims

    ctx = ctx_from_idp_claims({"sub": "u-new", "groups": []})
    assert ctx.role == "user"
    assert ctx.lab_ids == () and ctx.facility_ids == ()


def test_explicit_list_claims_beat_group_name_encoding():
    """An institution that configures the claim has said what it means."""
    from server.sso import ctx_from_idp_claims

    ctx = ctx_from_idp_claims(
        {"sub": "u-x", "groups": ["lab-lab-z"], "lab_ids": ["lab-a", "lab-b"]}
    )
    assert ctx.lab_ids == ("lab-a", "lab-b")


def test_unverified_claims_are_refused_outright():
    """A mapping that silently accepted unverified claims is the worst bug available."""
    from server.sso import ctx_from_idp_claims

    with pytest.raises(ValueError, match="verified"):
        ctx_from_idp_claims({"sub": "u-attacker", "groups": ["facility-admins"]}, verified=False)


def test_claims_without_a_subject_are_refused():
    from server.sso import ctx_from_idp_claims

    with pytest.raises(ValueError, match="missing"):
        ctx_from_idp_claims({"groups": ["facility-admins"]})
