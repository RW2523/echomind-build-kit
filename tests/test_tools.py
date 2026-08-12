"""M2 verification — happy paths for all 17 tools (pytest -m tools).

The two discovery tools (16, 17) have their own file, tests/test_discovery.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from server.db import session_scope
from server.mcp import tools as T
from server.mcp.errors import ToolError

pytestmark = pytest.mark.tools


def test_registry_has_exactly_seventeen_tools():
    """Fifteen became seventeen: find_facilities (16) and recommend_instrument (17).

    The count is asserted rather than derived so that a tool cannot be added, renamed or
    dropped without a human deciding it should be — the registry is the whole surface the
    agent and the MCP server expose, and numbering has to stay contiguous because it is
    how the specs and the tier matrix refer to each tool.
    """
    assert len(T.TOOLS) == 17
    assert sorted(s.number for s in T.TOOLS.values()) == list(range(1, 18))
    assert len(T.WRITE_TOOLS) == 4
    assert set(T.WRITE_TOOLS) == {
        "create_onboarding_request", "create_service_request",
        "request_booking", "generate_document",
    }


# --- read tools -------------------------------------------------------------------


def test_1_get_user_profile_self(ctxs):
    out = T.get_user_profile(ctxs["alice"], user_id="u-alice")
    assert out["user_id"] == "u-alice"
    assert out["lab"]["id"] == "lab-a"
    assert out["account_codes"] == ["ACC-A1"]


def test_1_get_user_profile_defaults_to_caller(ctxs):
    assert T.get_user_profile(ctxs["bob"])["user_id"] == "u-bob"


def test_2_get_facility_catalog(ctxs):
    out = T.get_facility_catalog(ctxs["alice"])
    assert len(out["facilities"]) == 3
    assert len(out["instruments"]) == 12
    assert len(out["templates"]) == 8
    assert any(i["name"] == "Confocal C2" for i in out["instruments"])


def test_2_get_facility_catalog_filtered(ctxs):
    out = T.get_facility_catalog(ctxs["alice"], facility_id="fac-genomics")
    assert [f["id"] for f in out["facilities"]] == ["fac-genomics"]
    assert all(i["facility_id"] == "fac-genomics" for i in out["instruments"])


def test_3_check_availability_returns_free_slots(ctxs):
    out = T.check_availability(
        ctxs["alice"], instrument_id="ins-miseq",
        date_from="2027-02-01T00:00:00Z", date_to="2027-02-03T00:00:00Z",
    )
    assert out["instrument"]["name"] == "MiSeq M3"
    assert out["free_slots"], "a wide-open future window should have free slots"
    for slot in out["free_slots"]:
        assert slot["starts_at"] < slot["ends_at"]


def test_3_check_availability_rejects_backwards_window(ctxs):
    with pytest.raises(ToolError) as exc:
        T.check_availability(ctxs["alice"], instrument_id="ins-miseq",
                             date_from="2027-02-03T00:00:00Z", date_to="2027-02-01T00:00:00Z")
    assert exc.value.code == "invalid_params"


def test_4_get_my_bookings(ctxs):
    out = T.get_my_bookings(ctxs["alice"])
    assert out["user_id"] == "u-alice"
    assert out["count"] > 0
    with session_scope() as s:
        expected = s.execute(
            text("SELECT count(*) FROM infinity.bookings WHERE user_id = 'u-alice'")
        ).scalar_one()
    assert out["count"] == expected


def test_5_get_usage_records_user_scope(ctxs):
    out = T.get_usage_records(ctxs["alice"], scope="user")
    assert out["scope"] == "user"
    assert out["id"] == "u-alice"
    assert out["totals"]["scheduled_hours"] >= 0
    assert all(r["user_id"] == "u-alice" for r in out["rows"])


def test_5_get_usage_records_lab_scope_for_pi(ctxs):
    out = T.get_usage_records(ctxs["asha"], scope="lab", id="lab-a")
    assert all(r["lab_id"] == "lab-a" for r in out["rows"])


def test_6_get_request_status_mine(ctxs):
    out = T.get_request_status(ctxs["alice"], mine=True)
    assert out["count"] > 0
    assert all("status" in r for r in out["requests"])


def test_6_get_request_status_by_id(ctxs):
    mine = T.get_request_status(ctxs["alice"], mine=True)["requests"][0]
    out = T.get_request_status(ctxs["alice"], request_id=mine["id"])["request"]
    assert out["id"] == mine["id"]
    assert out["user_id"] == "u-alice"
    assert isinstance(out["samples"], list)


def test_7_track_sample(ctxs):
    with session_scope() as s:
        barcode = s.execute(
            text(
                """SELECT s.barcode FROM infinity.samples s
                   JOIN infinity.service_requests sr ON sr.id = s.request_id
                   WHERE sr.user_id = 'u-alice' LIMIT 1"""
            )
        ).scalar_one()
    out = T.track_sample(ctxs["alice"], barcode=barcode)
    assert out["sample"]["barcode"] == barcode
    assert out["timeline"]


def test_8_get_billing_summary_own_code(ctxs):
    out = T.get_billing_summary(ctxs["alice"], account_code="ACC-A1", period="2026-03")
    assert out["lab_id"] == "lab-a"
    assert out["total"] > 0
    assert abs(sum(ln["amount"] for ln in out["lines"]) - out["total"]) < 0.01


def test_8_billing_march_confocal_story(ctxs):
    """The demo's precise number, straight from the tool."""
    out = T.get_billing_summary(ctxs["asha"], account_code="ACC-A1", period="2026-03")
    confocal = sum(ln["amount"] for ln in out["lines"] if ln["instrument"] == "Confocal C2")
    assert confocal == 412.00


def test_9_get_project_overview_admin(ctxs):
    out = T.get_project_overview(ctxs["cora"], project_id="prj-neuro-atlas")
    assert out["project"]["id"] == "prj-neuro-atlas"
    assert out["members"]
    assert "spend_basis" in out


def test_10_get_instrument_health_status_is_t0(ctxs):
    out = T.get_instrument_health(ctxs["alice"], instrument_id="ins-lightsheet")
    assert out["instrument"]["status"] == "maintenance"
    assert "history" not in out, "non-admins must not receive maintenance history"


def test_10_get_instrument_health_history_is_admin_only(ctxs):
    out = T.get_instrument_health(ctxs["cora"], instrument_id="ins-lightsheet")
    assert "history" in out
    assert out["downtime_hours_total"] >= 0


def test_11_run_readonly_sql_admin(ctxs):
    out = T.run_readonly_sql(ctxs["cora"], sql="SELECT * FROM v_bookings")
    assert out["row_count"] > 0
    assert "LIMIT 200" in out["executed_sql"]
    assert out["lab_filtered"] is False


# --- write tools: all four must only ever produce a pending action ------------------


def _pending_count() -> int:
    with session_scope() as s:
        return s.execute(
            text("SELECT count(*) FROM echomind.actions WHERE status = 'pending'")
        ).scalar_one()


def test_12_create_onboarding_request_is_pending_only(ctxs):
    before = _pending_count()
    out = T.create_onboarding_request(
        ctxs["asha"], name="New Person", email="new.person@example.edu",
        lab_id="lab-a", pi_ack=True, account_code="ACC-A1",
    )
    assert out["status"] == "pending"
    assert out["kind"] == "onboarding"
    assert _pending_count() == before + 1
    with session_scope() as s:
        assert s.execute(
            text("SELECT count(*) FROM infinity.users WHERE email = 'new.person@example.edu'")
        ).scalar_one() == 0, "nothing may be written before approval"


def test_12_onboarding_requires_pi_ack(ctxs):
    with pytest.raises(ToolError) as exc:
        T.create_onboarding_request(ctxs["asha"], name="X", email="x@example.edu",
                                    lab_id="lab-a", pi_ack=False)
    assert exc.value.code == "invalid_params"


def test_13_create_service_request_validates_required_fields(ctxs):
    with pytest.raises(ToolError) as exc:
        T.create_service_request(ctxs["alice"], template_id="tpl-rna-seq", fields={})
    assert exc.value.code == "invalid_params"
    assert "sample_count" in exc.value.message

    out = T.create_service_request(
        ctxs["alice"], template_id="tpl-rna-seq",
        fields={"sample_count": 8, "organism": "Mus musculus", "read_length": "100bp"},
    )
    assert out["status"] == "pending"
    assert out["kind"] == "service_request"


def test_13_create_service_request_rejects_bad_enum(ctxs):
    with pytest.raises(ToolError) as exc:
        T.create_service_request(
            ctxs["alice"], template_id="tpl-rna-seq",
            fields={"sample_count": 8, "organism": "Mus musculus", "read_length": "999bp"},
        )
    assert exc.value.code == "invalid_params"


def test_14_request_booking_is_pending_only(ctxs):
    before = _pending_count()
    out = T.request_booking(
        ctxs["alice"], instrument_id="ins-confocal-c2",
        starts_at="2027-03-04T09:00:00Z", ends_at="2027-03-04T11:00:00Z",
        account_code="ACC-A1",
    )
    assert out["status"] == "pending"
    assert out["kind"] == "booking"
    assert "Confocal C2" in out["payload_preview"]
    assert _pending_count() == before + 1
    with session_scope() as s:
        assert s.execute(
            text(
                "SELECT count(*) FROM infinity.bookings "
                "WHERE starts_at = '2027-03-04T09:00:00Z'"
            )
        ).scalar_one() == 0


def test_14_request_booking_rejects_unavailable_instrument(ctxs):
    with pytest.raises(ToolError) as exc:
        T.request_booking(ctxs["alice"], instrument_id="ins-lightsheet",
                          starts_at="2027-03-05T09:00:00Z", ends_at="2027-03-05T11:00:00Z",
                          account_code="ACC-A1")
    assert exc.value.code == "conflict"


def test_15_generate_document_is_pending_only(ctxs):
    out = T.generate_document(ctxs["alice"], template="usage_report", params={"month": "2026-03"})
    assert out["status"] == "pending"
    assert out["kind"] == "document"


def test_15_generate_document_rejects_unknown_template(ctxs):
    with pytest.raises(ToolError) as exc:
        T.generate_document(ctxs["alice"], template="salary_export")
    assert exc.value.code == "invalid_params"


# --- dispatch + errors --------------------------------------------------------------


def test_call_dispatch_matches_direct_call(ctxs):
    assert T.call(ctxs["alice"], "get_user_profile") == T.get_user_profile(ctxs["alice"])


def test_unknown_tool_is_invalid_params(ctxs):
    with pytest.raises(ToolError) as exc:
        T.call(ctxs["alice"], "drop_everything")
    assert exc.value.code == "invalid_params"


def test_error_object_shape():
    err = ToolError("forbidden", "nope", "hint").to_dict()
    assert set(err["error"]) == {"code", "message", "hint"}


# --- MCP surface --------------------------------------------------------------------


def test_mcp_exposes_every_registry_tool():
    import asyncio

    from server.mcp.server import mcp

    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == set(T.TOOLS)


def test_mcp_requires_a_bearer_token():
    """No ambient HTTP request => no identity => refusal. There is no anonymous access."""
    from server.mcp.server import _ctx_from_headers

    with pytest.raises(ToolError) as exc:
        _ctx_from_headers()
    assert exc.value.code == "unauthenticated"


# --- argument validation at the dispatch point ---------------------------------------


def test_unexpected_argument_is_a_typed_error_not_a_raw_typeerror(ctxs):
    """Regression: the UI showed a user "get_my_bookings() got an unexpected keyword
    argument 'subject_user_id'".

    `call` splatted the argument dict straight into the handler, so a stray key raised a
    bare TypeError whose text reached the screen — an internal signature shown where an
    honest refusal belonged.

    These messages are shown to the user when the data branch cannot repair the call, so
    they name the argument the way a person would say it — "a user", not
    `subject_user_id` and not "subject user id", which reads like a broken machine.
    """
    with pytest.raises(ToolError) as excinfo:
        T.call(ctxs["bob"], "get_my_bookings", {"subject_user_id": "u-alice"})
    err = excinfo.value
    assert err.code == "invalid_params"
    assert err.message == "That lookup does not take a user."
    assert "_" not in err.message, "schema spelling must not reach the reader"
    assert "a start date" in err.hint, "the hint should name what the tool does accept"


def test_a_missing_required_argument_is_also_a_typed_error(ctxs):
    """The mirror image. Checking one direction and not the other only moved the leak:
    get_project_overview with no project_id raised a bare TypeError from the handler."""
    with pytest.raises(ToolError) as excinfo:
        T.call(ctxs["cora"], "get_project_overview", {})
    assert excinfo.value.code == "invalid_params"
    assert excinfo.value.message == "That lookup needs a project."


def test_valid_arguments_still_dispatch(ctxs):
    """The guard must not reject legitimate calls."""
    out = T.call(ctxs["alice"], "get_my_bookings", {"date_from": "2026-01-01"})
    assert "bookings" in out


def test_no_argument_calls_still_dispatch(ctxs):
    assert "instruments" in T.call(ctxs["alice"], "get_facility_catalog", {})


def test_unknown_tool_is_rejected(ctxs):
    with pytest.raises(ToolError) as excinfo:
        T.call(ctxs["alice"], "drop_everything", {})
    assert excinfo.value.code == "invalid_params"


# --- generated documents render to the formats people actually send -------------------

DOC_MD = """# Usage report — Alice

User: `u-alice`
Period: 2026-03

| Instrument | Month | Scheduled h |
|---|---|---:|
| Confocal C2 | 2026-03 | 4.0 |
| _no usage_ | | |

- A bullet with **bold** in it

**Total scheduled:** 4.00 h
"""


def test_markdown_passes_through_untouched():
    from server.mcp import documents

    assert documents.render("t", DOC_MD, "md") == DOC_MD.encode("utf-8")


def test_docx_is_a_real_docx_carrying_the_table():
    """The templates build Markdown; a facility admin attaches a .docx to an email."""
    import io
    import zipfile

    from server.mcp import documents

    blob = documents.render("Usage report", DOC_MD, "docx")
    assert blob[:2] == b"PK", "a docx is a zip"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode()
    assert "<w:tbl>" in xml, "the pipe table must survive as a real table"
    assert "Confocal C2" in xml
    assert "Usage report" in xml


def test_pdf_is_a_real_pdf():
    from server.mcp import documents

    blob = documents.render("Usage report", DOC_MD, "pdf")
    assert blob[:5] == b"%PDF-"
    assert len(blob) > 1000, "a one-page report should not be empty"


def test_a_short_table_row_does_not_break_rendering():
    """The usage template emits a placeholder row with fewer cells than the header."""
    from server.mcp import documents

    for fmt in ("docx", "pdf"):
        assert documents.render("t", DOC_MD, fmt)


def test_an_unsupported_format_is_refused(ctxs):
    with pytest.raises(ToolError) as excinfo:
        T.generate_document(ctxs["alice"], template="usage_report", format="rtf")
    assert excinfo.value.code == "invalid_params"
    assert "md, docx, pdf" in excinfo.value.message


def test_the_format_reaches_the_approval_preview(ctxs):
    """What the approver sees has to say which artefact they are agreeing to."""
    pending = T.generate_document(ctxs["alice"], template="usage_report", format="pdf")
    assert "PDF" in pending["payload_preview"]
    assert pending["payload"]["format"] == "pdf"


def test_a_single_bare_date_means_that_whole_day(ctxs):
    """Regression: "Is it free on Thursday?" makes a planner write the same date twice,
    and rejecting that as "date_to must be after date_from" is technically correct and
    useless — the caller asked a perfectly clear question. It made a demo scene fail
    about one run in five."""
    out = T.check_availability(
        ctxs["alice"], instrument_id="ins-confocal-c2",
        date_from="2027-12-02", date_to="2027-12-02",
    )
    assert "free_slots" in out


def test_the_same_instant_twice_is_still_an_error(ctxs):
    """A zero-length window with times on it is a mistake, not a day."""
    with pytest.raises(ToolError) as excinfo:
        T.check_availability(
            ctxs["alice"], instrument_id="ins-confocal-c2",
            date_from="2027-12-02T14:00", date_to="2027-12-02T14:00",
        )
    assert excinfo.value.code == "invalid_params"


def test_a_backwards_range_is_still_an_error(ctxs):
    with pytest.raises(ToolError):
        T.check_availability(
            ctxs["alice"], instrument_id="ins-confocal-c2",
            date_from="2027-12-05", date_to="2027-12-02",
        )


# --- regressions from the 2026-08-11 conversation review --------------------------


def test_availability_offers_no_slots_on_an_instrument_under_maintenance(ctxs):
    """The bug: free slots are gaps in the calendar, and a broken instrument has plenty.

    check_availability reported 08:00-20:00 free on an instrument in maintenance, and the
    agent duly told the user it was available one turn after correctly refusing to book
    it. The tool result was wrong, so the answer was confidently wrong with an evidence
    table under it.
    """
    out = T.check_availability(
        ctxs["alice"], instrument_id="ins-lightsheet",
        date_from="2027-03-31T00:00:00Z", date_to="2027-04-01T00:00:00Z",
    )
    assert out["instrument"]["status"] == "maintenance"
    assert out["bookable"] is False
    assert out["free_slots"] == [], "a machine nobody may book has no free time"
    assert out["requested_window_free"] is False
    assert "maintenance" in out["unavailable_reason"]


def test_availability_says_why_rather_than_only_that_it_is_not_free(ctxs):
    """free = False beside conflicts = 0 reads as a contradiction without the reason."""
    out = T.check_availability(
        ctxs["alice"], instrument_id="ins-qtof",
        date_from="2027-03-31T00:00:00Z", date_to="2027-04-01T00:00:00Z",
    )
    assert out["conflicting_bookings"] == 0
    assert out["requested_window_free"] is False
    assert out["unavailable_reason"] and "offline" in out["unavailable_reason"]


def test_availability_still_offers_slots_on_a_working_instrument(ctxs):
    """The guard must not silence the ordinary case."""
    out = T.check_availability(
        ctxs["alice"], instrument_id="ins-miseq",
        date_from="2027-02-01T00:00:00Z", date_to="2027-02-03T00:00:00Z",
    )
    assert out["bookable"] is True
    assert out["unavailable_reason"] is None
    assert out["free_slots"]


def test_booking_outside_opening_hours_is_refused(ctxs):
    """check_availability published 08:00-20:00; request_booking accepted 03:00 anyway."""
    with pytest.raises(ToolError) as exc:
        T.request_booking(
            ctxs["alice"], instrument_id="ins-miseq",
            starts_at="2027-02-01T03:00:00Z", ends_at="2027-02-01T05:00:00Z",
            account_code="ACC-A1",
        )
    assert "opening hours" in exc.value.message


def test_booking_may_not_run_past_closing(ctxs):
    with pytest.raises(ToolError) as exc:
        T.request_booking(
            ctxs["alice"], instrument_id="ins-miseq",
            starts_at="2027-02-01T19:00:00Z", ends_at="2027-02-01T21:00:00Z",
            account_code="ACC-A1",
        )
    assert "opening hours" in exc.value.message


def test_booking_may_not_span_midnight(ctxs):
    """Under 12 hours, so the duration rule does not catch it."""
    with pytest.raises(ToolError) as exc:
        T.request_booking(
            ctxs["alice"], instrument_id="ins-miseq",
            starts_at="2027-02-01T19:00:00Z", ends_at="2027-02-02T02:00:00Z",
            account_code="ACC-A1",
        )
    assert "same day" in exc.value.message


def test_a_slot_availability_offered_is_a_slot_booking_accepts(ctxs):
    """The two tools must agree: anything check_availability offers must be bookable.

    This is the invariant both fixes exist to protect. It is asserted rather than
    described because the pair drifted apart once already.
    """
    out = T.check_availability(
        ctxs["alice"], instrument_id="ins-miseq",
        date_from="2027-02-01T00:00:00Z", date_to="2027-02-02T00:00:00Z",
    )
    for slot in out["free_slots"]:
        pending = T.request_booking(
            ctxs["alice"], instrument_id="ins-miseq",
            starts_at=slot["starts_at"], ends_at=slot["ends_at"],
            account_code="ACC-A1",
        )
        assert pending["action_id"]
