"""Pending actions: creation, approval, decline, execution, audit.

Golden rule 4: every write tool returns a pending action. Execution happens only after
explicit approval via the API, and every event — proposed, approved, declined, executed,
failed — lands in the audit table.

Nothing here trusts the model: the payload was validated by the tool handler at proposal
time and is re-validated against the caller's tier at approval time.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from server.agent import memory
from server.auth import Ctx
from server.config import REPO_ROOT
from server.db import session_scope
from server.mcp import documents
from server.mcp.errors import ToolError, forbidden, invalid_params, not_found

log = logging.getLogger("echomind.actions")

OUTPUTS_DIR = REPO_ROOT / "files" / "outputs"

# tool name -> action kind (spec 02, write tools table)
ACTION_KIND = {
    "create_onboarding_request": "onboarding",
    "create_service_request": "service_request",
    "request_booking": "booking",
    "generate_document": "document",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return f"act-{uuid.uuid4().hex[:12]}"


def audit(conn, action_id: str | None, event: str, actor_id: str,
          tool: str | None = None, detail: dict | None = None) -> None:
    conn.execute(
        text(
            """INSERT INTO echomind.audit_log (action_id, event, actor_id, tool, detail)
               VALUES (:aid, :event, :actor, :tool, CAST(:detail AS jsonb))"""
        ),
        {
            "aid": action_id,
            "event": event,
            "actor": actor_id,
            "tool": tool,
            "detail": json.dumps(detail or {}),
        },
    )


def create_pending(ctx: Ctx, tool: str, payload: dict[str, Any],
                   preview: str) -> dict[str, Any]:
    """Insert a pending action. Touches nothing in infinity.*."""
    action_id = _new_id()
    with session_scope() as s:
        s.execute(
            text(
                """INSERT INTO echomind.actions (id, user_id, tool, payload, status)
                   VALUES (:id, :uid, :tool, CAST(:payload AS jsonb), 'pending')"""
            ),
            {"id": action_id, "uid": ctx.user_id, "tool": tool, "payload": json.dumps(payload)},
        )
        audit(s, action_id, "proposed", ctx.user_id, tool,
              {"kind": ACTION_KIND.get(tool), "payload": payload})

    log.info("action %s proposed by %s via %s", action_id, ctx.user_id, tool)
    return {
        "action_id": action_id,
        "status": "pending",
        "kind": ACTION_KIND.get(tool),
        "tool": tool,
        "payload_preview": preview,
        "payload": payload,
        "message": (
            "This needs your approval before anything changes. "
            "Nothing has been written yet."
        ),
    }


def get_action(action_id: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.execute(
            text(
                """SELECT id, user_id, tool, payload, status, approver_id,
                          created_at, decided_at, executed_at, result, thread_id
                   FROM echomind.actions WHERE id = :id"""
            ),
            {"id": action_id},
        ).mappings().first()
    return dict(row) if row else None


def list_actions(ctx: Ctx, limit: int = 50) -> list[dict[str, Any]]:
    """Admins see everything; everyone else sees only their own."""
    sql = """SELECT id, user_id, tool, payload, status, approver_id,
                    created_at, decided_at, executed_at, result, thread_id
             FROM echomind.actions {where}
             ORDER BY created_at DESC LIMIT :limit"""
    params: dict[str, Any] = {"limit": limit}
    if ctx.is_admin:
        sql = sql.format(where="")
    else:
        sql = sql.format(where="WHERE user_id = :uid")
        params["uid"] = ctx.user_id
    with session_scope() as s:
        rows = s.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def audit_trail(ctx: Ctx, action_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    clauses, params = [], {"limit": limit}
    if action_id:
        clauses.append("a.action_id = :aid")
        params["aid"] = action_id
    if not ctx.is_admin:
        clauses.append("(act.user_id = :uid OR a.actor_id = :uid)")
        params["uid"] = ctx.user_id
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with session_scope() as s:
        rows = s.execute(
            text(
                f"""SELECT a.id, a.action_id, a.event, a.actor_id, a.tool, a.detail, a.created_at
                    FROM echomind.audit_log a
                    LEFT JOIN echomind.actions act ON act.id = a.action_id
                    {where}
                    ORDER BY a.created_at DESC, a.id DESC LIMIT :limit"""
            ),
            params,
        ).mappings().all()
    return [dict(r) for r in rows]


def _assert_may_decide(ctx: Ctx, action: dict) -> None:
    """Spec 02: the requesting user may decide their own action; admins may decide any."""
    if ctx.is_admin or action["user_id"] == ctx.user_id:
        return
    raise forbidden()


def decline(ctx: Ctx, action_id: str) -> dict[str, Any]:
    action = get_action(action_id)
    if action is None:
        raise not_found("action")
    _assert_may_decide(ctx, action)
    if action["status"] != "pending":
        raise ToolError(
            "conflict",
            f"This action is already {action['status']}.",
            "Only a pending action can be declined.",
        )

    with session_scope() as s:
        s.execute(
            text(
                """UPDATE echomind.actions
                   SET status = 'declined', approver_id = :actor, decided_at = :now
                   WHERE id = :id"""
            ),
            {"actor": ctx.user_id, "now": _now(), "id": action_id},
        )
        audit(s, action_id, "declined", ctx.user_id, action["tool"], {})

    log.info("action %s declined by %s", action_id, ctx.user_id)
    return {"action_id": action_id, "status": "declined"}


def approve(ctx: Ctx, action_id: str) -> dict[str, Any]:
    """pending -> approved -> executed (or failed). Every hop is audited."""
    action = get_action(action_id)
    if action is None:
        raise not_found("action")
    _assert_may_decide(ctx, action)
    if action["status"] != "pending":
        raise ToolError(
            "conflict",
            f"This action is already {action['status']}.",
            "Only a pending action can be approved.",
        )

    with session_scope() as s:
        s.execute(
            text(
                """UPDATE echomind.actions
                   SET status = 'approved', approver_id = :actor, decided_at = :now
                   WHERE id = :id"""
            ),
            {"actor": ctx.user_id, "now": _now(), "id": action_id},
        )
        audit(s, action_id, "approved", ctx.user_id, action["tool"], {})

    try:
        result = _execute(action)
    except Exception as exc:
        log.exception("action %s failed to execute", action_id)
        with session_scope() as s:
            s.execute(
                text(
                    """UPDATE echomind.actions
                       SET status = 'failed', executed_at = :now,
                           result = CAST(:result AS jsonb)
                       WHERE id = :id"""
                ),
                {"now": _now(), "result": json.dumps({"error": str(exc)}), "id": action_id},
            )
            audit(s, action_id, "failed", ctx.user_id, action["tool"], {"error": str(exc)})
        return {"action_id": action_id, "status": "failed", "error": str(exc)}

    # Learn the caller's preferences from what they actually approved — never from what
    # was merely proposed. Preferences only pre-fill the next proposal; they are never
    # read back as facts. See server/agent/memory.py.
    memory.learn_from_execution(action["user_id"], action["tool"], action["payload"], result)

    with session_scope() as s:
        s.execute(
            text(
                """UPDATE echomind.actions
                   SET status = 'executed', executed_at = :now, result = CAST(:result AS jsonb)
                   WHERE id = :id"""
            ),
            {"now": _now(), "result": json.dumps(result, default=str), "id": action_id},
        )
        audit(s, action_id, "executed", ctx.user_id, action["tool"], result)

    log.info("action %s executed by %s", action_id, ctx.user_id)
    return {"action_id": action_id, "status": "executed", "result": result}


# --- execution -------------------------------------------------------------------


def _execute(action: dict) -> dict[str, Any]:
    tool = action["tool"]
    payload = action["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    handler = {
        "create_onboarding_request": _exec_onboarding,
        "create_service_request": _exec_service_request,
        "request_booking": _exec_booking,
        "cancel_booking": _exec_cancel_booking,
        "reschedule_booking": _exec_reschedule_booking,
        "generate_document": _exec_document,
    }.get(tool)
    if handler is None:
        raise invalid_params(f"No execution mapping for tool {tool!r}.")
    return handler(action, payload)


def _exec_onboarding(action: dict, payload: dict) -> dict[str, Any]:
    user_id = payload.get("user_id") or f"u-{uuid.uuid4().hex[:8]}"
    with session_scope() as s:
        exists = s.execute(
            text("SELECT 1 FROM infinity.users WHERE email = :email"),
            {"email": payload["email"]},
        ).first()
        if exists:
            raise ToolError("conflict", "A user with that email already exists.", "")
        s.execute(
            text(
                """INSERT INTO infinity.users
                   (id, email, name, role, lab_id, training, account_codes)
                   VALUES (:id, :email, :name, 'user', :lab_id,
                           CAST(:training AS jsonb), :codes)"""
            ),
            {
                "id": user_id,
                "email": payload["email"],
                "name": payload["name"],
                "lab_id": payload["lab_id"],
                "training": json.dumps({}),
                "codes": [payload["account_code"]] if payload.get("account_code") else [],
            },
        )
    return {"created": "user", "user_id": user_id, "email": payload["email"]}


def _exec_service_request(action: dict, payload: dict) -> dict[str, Any]:
    request_id = f"sr-{uuid.uuid4().hex[:8]}"
    now = _now()
    history = [{"at": now.isoformat(), "status": "submitted", "by": action["user_id"]}]
    with session_scope() as s:
        s.execute(
            text(
                """INSERT INTO infinity.service_requests
                   (id, user_id, template_id, fields, status, history)
                   VALUES (:id, :uid, :tpl, CAST(:fields AS jsonb), 'submitted',
                           CAST(:history AS jsonb))"""
            ),
            {
                "id": request_id,
                "uid": action["user_id"],
                "tpl": payload["template_id"],
                "fields": json.dumps(payload.get("fields") or {}),
                "history": json.dumps(history),
            },
        )
    return {"created": "service_request", "request_id": request_id,
            "template_id": payload["template_id"], "status": "submitted"}


def _exec_booking(action: dict, payload: dict) -> dict[str, Any]:
    booking_id = f"bk-{uuid.uuid4().hex[:8]}"
    with session_scope() as s:
        clash = s.execute(
            text(
                """SELECT id FROM infinity.bookings
                   WHERE instrument_id = :iid
                     AND status IN ('requested', 'confirmed')
                     AND starts_at < CAST(:ends AS timestamptz)
                     AND ends_at   > CAST(:starts AS timestamptz)
                   LIMIT 1"""
            ),
            {"iid": payload["instrument_id"], "starts": payload["starts_at"],
             "ends": payload["ends_at"]},
        ).first()
        if clash:
            raise ToolError(
                "conflict",
                "That slot was taken while the request was awaiting approval.",
                "Pick another slot with check_availability.",
            )
        # Status 'requested', not 'confirmed' (spec 08 scene 2): approval is the *user*
        # agreeing to the agent's proposal. The facility still confirms the slot.
        s.execute(
            text(
                """INSERT INTO infinity.bookings
                   (id, user_id, instrument_id, starts_at, ends_at, status, account_code)
                   VALUES (:id, :uid, :iid, CAST(:starts AS timestamptz),
                           CAST(:ends AS timestamptz), 'requested', :code)"""
            ),
            {
                "id": booking_id,
                "uid": action["user_id"],
                "iid": payload["instrument_id"],
                "starts": payload["starts_at"],
                "ends": payload["ends_at"],
                "code": payload.get("account_code"),
            },
        )
    return {
        "created": "booking",
        "booking_id": booking_id,
        "instrument_id": payload["instrument_id"],
        "starts_at": payload["starts_at"],
        "ends_at": payload["ends_at"],
        "status": "requested",
    }


def _exec_cancel_booking(action: dict, payload: dict) -> dict[str, Any]:
    """Cancel it, recording the charge the user was shown when they approved.

    Re-checked at execution rather than trusted from the payload: an approval can be acted
    on long after it was proposed, and a booking that was cancelled or completed in the
    meantime must not be cancelled twice. The scope was already enforced when the action
    was created, and `user_id` is carried on the action row.
    """
    booking_id = payload["booking_id"]
    with session_scope() as s:
        row = s.execute(
            text("SELECT status FROM infinity.bookings WHERE id = :bid"),
            {"bid": booking_id},
        ).mappings().first()
        if row is None:
            raise not_found("booking")
        previous_status = row["status"]
        if row["status"] in ("cancelled", "completed"):
            raise ToolError(
                "conflict",
                f"That booking is already {row['status']} — nothing was changed.",
                "Check the booking before trying again.",
            )
        s.execute(
            text("UPDATE infinity.bookings SET status = 'cancelled' WHERE id = :bid"),
            {"bid": booking_id},
        )

    # The policy as it was applied travels into the result, so the audit row answers
    # "why was I charged for this?" without anyone having to reconstruct the rule.
    applied = ((payload.get("policy") or {}).get("applied")) or {}
    return {
        "cancelled": booking_id,
        # What it was before. Approving an action mutates seeded platform data, and the
        # suite's tear-down can only undo what the result describes — it knew how to
        # delete a booking that was created and nothing about one that was changed. So
        # every run cancelled a few more until all 200 were cancelled and the occupancy
        # view was empty. A change has to say what it changed from.
        "previous_status": previous_status,
        "reason": payload.get("reason"),
        "charge_percent": (payload.get("policy") or {}).get("charge_percent"),
        "charged_hours": (payload.get("policy") or {}).get("charged_hours"),
        "policy_applied": applied.get("id"),
        "policy_statement": applied.get("statement"),
        "policy_source": applied.get("source_doc_id"),
    }


def _exec_reschedule_booking(action: dict, payload: dict) -> dict[str, Any]:
    """Move it — one statement, so the old slot is never released without the new one taken.

    The rules call this a cancellation plus a new booking, but doing it as two writes would
    leave a window where the user has neither. It stays a single UPDATE inside one
    transaction; the charge on the released time is recorded from what they approved.
    """
    booking_id = payload["booking_id"]
    with session_scope() as s:
        row = s.execute(
            text("""SELECT status, instrument_id, starts_at, ends_at
                    FROM infinity.bookings WHERE id = :bid"""),
            {"bid": booking_id},
        ).mappings().first()
        if row is None:
            raise not_found("booking")
        if row["status"] in ("cancelled", "completed"):
            raise ToolError(
                "conflict",
                f"That booking is already {row['status']} — nothing was changed.",
                "Book a new session instead.",
            )
        clash = s.execute(
            text(
                """SELECT id FROM infinity.bookings
                   WHERE instrument_id = :iid AND id <> :bid
                     AND status IN ('requested', 'confirmed')
                     AND starts_at < CAST(:ends AS timestamptz)
                     AND ends_at   > CAST(:starts AS timestamptz)
                   LIMIT 1"""
            ),
            {"iid": row["instrument_id"], "bid": booking_id,
             "starts": payload["starts_at"], "ends": payload["ends_at"]},
        ).first()
        if clash:
            raise ToolError(
                "conflict",
                "That slot was taken while the request was awaiting approval.",
                "Pick another slot with check_availability.",
            )
        s.execute(
            text(
                """UPDATE infinity.bookings
                   SET starts_at = CAST(:starts AS timestamptz),
                       ends_at   = CAST(:ends AS timestamptz),
                       status    = 'requested'
                   WHERE id = :bid"""
            ),
            {"bid": booking_id, "starts": payload["starts_at"], "ends": payload["ends_at"]},
        )
        was_from, was_to = row["starts_at"].isoformat(), row["ends_at"].isoformat()
        previous_status = row["status"]

    release = (payload.get("policy") or {}).get("release") or {}
    applied = release.get("applied") or {}
    return {
        "rescheduled": booking_id,
        "previous_status": previous_status,
        "moved_from": {"starts_at": was_from, "ends_at": was_to},
        "moved_to": {"starts_at": payload["starts_at"], "ends_at": payload["ends_at"]},
        "status": "requested",
        "release_charge_percent": release.get("charge_percent"),
        "policy_applied": applied.get("id"),
        "policy_statement": applied.get("statement"),
    }


def _exec_document(action: dict, payload: dict) -> dict[str, Any]:
    """Render to files/outputs/. Every value in the document comes from a query."""
    template = payload["template"]
    params = payload.get("params") or {}
    user_id = action["user_id"]
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    renderer = {
        "usage_report": _render_usage_report,
        "onboarding_packet": _render_onboarding_packet,
        "monthly_summary": _render_monthly_summary,
        "invoice_statement": _render_invoice_statement,
        "facility_directory": _render_facility_directory,
        "capability_report": _render_capability_report,
        "booking_confirmation": _render_booking_confirmation,
        "usage_summary": _render_usage_summary,
    }[template]
    rendered = renderer(user_id, params)
    # Renderers that query return the rows they used as a third element. A document is a
    # claim about records; being able to see those records is what separates it from a
    # nicely formatted guess, so they travel with the result rather than being re-derived.
    title, body = rendered[0], rendered[1]
    # A renderer may hand back its own PDF layout as a fourth element. An invoice that
    # arrives looking like a memo reads as a draft, and the layout is part of whether the
    # figures are believed — so the one document that is a form gets composed as one.
    layout = rendered[3] if len(rendered) > 3 else None
    records = [_plain(r) for r in (rendered[2] if len(rendered) > 2 else [])]
    record_count = len(records)
    # The action row is an audit record, not a data warehouse. A directory of every core
    # on campus would bloat every row that carries it, so the evidence list is capped and
    # says so — the count above stays truthful about how many rows the document used.
    records = records[:_MAX_RECORDS]

    # The templates produce Markdown and always did; what changed is that the artefact a
    # facility admin actually attaches to an email is a .docx or a .pdf, so the same body
    # is rendered into whichever was asked for. Markdown stays the default, because the
    # demo and the tests read it.
    fmt = str(payload.get("format") or documents.DEFAULT_FORMAT).lower()
    if fmt not in documents.FORMATS:
        raise invalid_params(
            f"Unsupported document format {fmt!r}.",
            f"Choose one of: {', '.join(documents.FORMATS)}.",
        )

    stamp = _now().strftime("%Y%m%dT%H%M%S")
    path = OUTPUTS_DIR / f"{template}-{stamp}-{action['id']}{documents.EXTENSION[fmt]}"
    path.write_bytes(
        layout() if (fmt == "pdf" and layout) else documents.render(title, body, fmt)
    )

    return {
        "created": "document",
        "records": records,
        "record_count": record_count,
        "records_truncated": record_count > len(records),
        "template": template,
        "title": title,
        "format": fmt,
        "path": str(path.relative_to(REPO_ROOT)),
        "absolute_path": str(path),
        "bytes": path.stat().st_size,
        # The route that serves this file. Carried on the result so the confirmation the
        # user reads can be a link they can click, rather than a path on a machine they
        # have no account on.
        "download_url": f"/actions/{action['id']}/document",
        "filename": path.name,
    }


def _render_usage_report(user_id: str, params: dict) -> tuple[str, str]:
    target = params.get("user_id") or user_id
    month = params.get("month")
    with session_scope() as s:
        who = s.execute(
            text("SELECT name, lab_id FROM infinity.users WHERE id = :id"), {"id": target}
        ).mappings().first()
        rows = s.execute(
            text(
                """SELECT instrument, month, scheduled_hours, tracked_hours
                   FROM reporting.v_usage_summary
                   WHERE user_id = :uid AND (CAST(:month AS text) IS NULL OR month = :month)
                   ORDER BY month DESC, instrument"""
            ),
            {"uid": target, "month": month},
        ).mappings().all()

    name = who["name"] if who else target
    lines = [
        f"# Usage report — {name}",
        "",
        f"User: `{target}`  ",
        f"Lab: `{who['lab_id'] if who else 'n/a'}`  ",
        f"Period: {month or 'all recorded months'}  ",
        f"Generated: {_now().isoformat(timespec='seconds')}",
        "",
        "| Instrument | Month | Scheduled h | Tracked h |",
        "|---|---|---:|---:|",
    ]
    total_s = total_t = 0.0
    for r in rows:
        total_s += float(r["scheduled_hours"] or 0)
        total_t += float(r["tracked_hours"] or 0)
        lines.append(
            f"| {r['instrument']} | {r['month']} | {r['scheduled_hours']} | {r['tracked_hours']} |"
        )
    if not rows:
        lines.append("| _no usage recorded_ | | | |")
    lines += ["", f"**Total scheduled:** {total_s:.2f} h  ", f"**Total tracked:** {total_t:.2f} h"]
    return f"Usage report — {name}", "\n".join(lines) + "\n"


def _render_onboarding_packet(user_id: str, params: dict) -> tuple[str, str]:
    lab_id = params.get("lab_id")
    with session_scope() as s:
        lab = s.execute(
            text("SELECT name FROM infinity.labs WHERE id = :id"), {"id": lab_id}
        ).scalar_one_or_none()
        facilities = s.execute(
            text("SELECT name, code FROM infinity.facilities ORDER BY name")
        ).mappings().all()
        instruments = s.execute(
            text(
                """SELECT i.name, i.hourly_rate, f.name AS facility
                   FROM infinity.instruments i
                   JOIN infinity.facilities f ON f.id = i.facility_id
                   ORDER BY f.name, i.name"""
            )
        ).mappings().all()

    lines = [
        f"# Onboarding packet — {params.get('name', 'new user')}",
        "",
        f"Lab: {lab or lab_id or 'n/a'}  ",
        f"Generated: {_now().isoformat(timespec='seconds')}",
        "",
        "## Facilities",
        "",
    ]
    lines += [f"- {f['name']} (`{f['code']}`)" for f in facilities]
    lines += ["", "## Instruments and rates", "", "| Instrument | Facility | Rate / h |",
              "|---|---|---:|"]
    lines += [f"| {i['name']} | {i['facility']} | ${i['hourly_rate']} |" for i in instruments]
    lines += ["", "## Next steps", "",
              "1. Complete biosafety training before booking any instrument.",
              "2. Confirm your account code with your PI.",
              "3. Book induction time on your primary instrument."]
    return "Onboarding packet", "\n".join(lines) + "\n"


def _render_monthly_summary(user_id: str, params: dict) -> tuple[str, str]:
    period = params.get("period") or "2026-03"
    with session_scope() as s:
        billing = s.execute(
            text(
                """SELECT lab_id, sum(amount) AS total
                   FROM reporting.v_billing_lines WHERE period = :p
                   GROUP BY lab_id ORDER BY total DESC"""
            ),
            {"p": period},
        ).mappings().all()
        downtime = s.execute(
            text(
                """SELECT instrument, facility, downtime_hours, repair_count
                   FROM reporting.v_instrument_downtime WHERE month = :p
                   ORDER BY downtime_hours DESC LIMIT 10"""
            ),
            {"p": period},
        ).mappings().all()

    lines = [
        f"# Monthly summary — {period}",
        "",
        f"Generated: {_now().isoformat(timespec='seconds')}",
        "",
        "## Billing by lab",
        "",
        "| Lab | Total |",
        "|---|---:|",
    ]
    lines += [f"| {b['lab_id']} | ${b['total']} |" for b in billing]
    if not billing:
        lines.append("| _no billing lines_ | |")
    lines += ["", "## Instrument downtime", "",
              "| Instrument | Facility | Downtime h | Repairs |", "|---|---|---:|---:|"]
    lines += [
        f"| {d['instrument']} | {d['facility']} | {d['downtime_hours']} | {d['repair_count']} |"
        for d in downtime
    ]
    if not downtime:
        lines.append("| _no maintenance events_ | | | |")
    return f"Monthly summary — {period}", "\n".join(lines) + "\n"

_MAX_RECORDS = 50


def _plain(value: Any) -> Any:
    """Money, dates and ids as JSON, without losing what they meant.

    The billing views return Decimal and the booking rows return timestamps; both go into
    the action's result column, which is JSON. Rounding money to float here would be a
    quiet lie in a document about money, so Decimal becomes a string and keeps every digit
    it was given.
    """
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _render_invoice_statement(user_id: str, params: dict) -> tuple:
    """A statement for one account and period, from the ledger the invoice was built from.

    The builders in server/mcp/documents.py deliberately do not query — they compose from
    data handed to them — so the fetch lives here, beside the other renderers, and the
    caller's entitlement is checked by the same tool the chat path uses rather than by a
    second copy of the rule.
    """
    account_code = params.get("account_code")
    period = params.get("period")
    if not account_code or not period:
        raise invalid_params(
            "An invoice statement needs an account code and a period.",
            "For example account ACC-A1, period 2026-03.",
        )
    from server.mcp import tools as tools_mod  # deferred: tools imports this module

    ctx = _ctx_for(user_id)
    summary = tools_mod.get_billing_summary(ctx, account_code=account_code, period=period)
    lines = summary.get("lines") or []
    fields = {
        "account_code": account_code, "period": period, "lines": lines,
        "total": summary.get("total"), "lab": summary.get("lab_id"),
    }
    body = documents.build_invoice_statement(**fields)
    return (
        f"Invoice statement — {account_code} {period}",
        body,
        lines,
        lambda: documents.invoice_pdf(
            **fields, prepared=_now().strftime("%d %B %Y")
        ),
    )


def _render_facility_directory(user_id: str, params: dict) -> tuple[str, str, list]:
    """Where every core is and what it holds — the printable form of find_facilities."""
    from server.mcp import tools as tools_mod  # deferred: tools imports this module

    ctx = _ctx_for(user_id)
    found = tools_mod.find_facilities(
        ctx,
        technique=params.get("technique"),
        campus=params.get("campus"),
        near_latitude=params.get("near_latitude"),
        near_longitude=params.get("near_longitude"),
    )
    facilities = found.get("facilities") or []
    body = documents.build_facility_directory(facilities)
    return "Facility directory", body, facilities


def _render_capability_report(user_id: str, params: dict) -> tuple[str, str, list]:
    """What can do a stated job, where it is, and why each one qualified."""
    goal = params.get("goal")
    if not goal:
        raise invalid_params(
            "A capability report needs to know what you want to do.",
            'For example goal "cryo-EM of a protein complex".',
        )
    from server.mcp import tools as tools_mod  # deferred: tools imports this module

    ctx = _ctx_for(user_id)
    found = tools_mod.recommend_instrument(
        ctx, goal=goal, sample_type=params.get("sample_type")
    )
    matches = found.get("matches") or []
    body = documents.build_capability_report(goal, matches)
    return f"Capability report — {goal}", body, matches


def _ctx_for(user_id: str) -> Ctx:
    """The stored actor's context, rebuilt from the database at execution time.

    An approval can be executed long after it was proposed, so the role and labs are read
    fresh rather than carried on the action row: a user who lost access between proposing
    and approving must not have the old entitlement honoured.
    """
    with session_scope() as s:
        row = s.execute(
            text("SELECT id, name, role, lab_id FROM infinity.users WHERE id = :id"),
            {"id": user_id},
        ).mappings().first()
    if row is None:
        raise not_found("user")
    return Ctx(
        user_id=row["id"], name=row["name"], role=row["role"],
        lab_ids=(row["lab_id"],) if row["lab_id"] else (),
        facility_ids=(), raw={},
    )


def _render_booking_confirmation(user_id: str, params: dict) -> tuple[str, str, list]:
    """The confirmation for one booking, including where the instrument physically is.

    Scoped by the caller in SQL rather than fetched and then checked: a booking that is not
    theirs is not found, which is the same answer whether it exists or not.
    """
    booking_id = params.get("booking_id")
    if not booking_id:
        raise invalid_params(
            "A booking confirmation needs to know which booking.",
            "Give the booking id, e.g. bk-0133.",
        )
    with session_scope() as s:
        row = s.execute(
            text(
                """SELECT b.id, b.starts_at, b.ends_at, b.status, b.account_code,
                          i.name AS instrument, i.room, f.name AS facility,
                          f.campus, f.building, f.address
                   FROM infinity.bookings b
                   JOIN infinity.instruments i ON i.id = b.instrument_id
                   JOIN infinity.facilities f ON f.id = i.facility_id
                   WHERE b.id = :bid AND b.user_id = :uid"""
            ),
            {"bid": booking_id, "uid": user_id},
        ).mappings().first()
    if row is None:
        raise not_found("booking")
    record = dict(row)
    body = documents.build_booking_confirmation(record)
    return f"Booking confirmation — {booking_id}", body, [record]


def _render_usage_summary(user_id: str, params: dict) -> tuple[str, str, list]:
    """Scheduled versus tracked hours, through the tool that already enforces the scope."""
    from server.mcp import tools as tools_mod  # deferred: tools imports this module

    ctx = _ctx_for(user_id)
    scope = params.get("scope") or "user"
    found = tools_mod.get_usage_records(
        ctx, scope=scope, id=params.get("id"), month=params.get("month")
    )
    subject = found.get("id") or user_id
    rows = found.get("rows") or []
    body = documents.build_usage_summary(
        subject, params.get("month"), rows, found.get("totals") or {}
    )
    return f"Usage summary — {subject}", body, rows
