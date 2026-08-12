"""The 17 tools.

Every handler takes a verified `Ctx` and enforces its tier BEFORE running any query
(spec 02). These functions are the single implementation: the MCP server exposes them
over the wire and the LangGraph agent calls them in-process, so there is exactly one
enforcement path and one thing to test.

Tier matrix — spec 05:
    T0  any authenticated user            2, 3, 10(status), 16, 17
    T1  caller's own data                 1(self), 4, 5(user), 6(mine), 7, 8(own codes),
                                          12, 13, 14, 15(user templates)
    T2  pi, within their lab_ids          1, 5, 6, 7, 8, 9 + run_readonly_sql (rewritten)
    T3  admin                             everything, 10(history), unrestricted SQL
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from server.agent import memory
from server.auth import Ctx
from server.db import ro_session, session_scope
from server.mcp import actions as actions_mod
from server.mcp import documents
from server.mcp.errors import ToolError, forbidden, invalid_params, not_found
from server.mcp.sql_guard import MAX_ROWS
from server.mcp.sql_guard import validate as validate_sql
from server.observability import traced_tool

log = logging.getLogger("echomind.tools")

# Facility opening hours used to derive free slots (tool 3).
OPEN_HOUR, CLOSE_HOUR = 8, 20

DOCUMENT_TEMPLATES = (
    "usage_report", "onboarding_packet", "monthly_summary",
    # Feature documents: the printable form of what the chat path already answers.
    "invoice_statement", "facility_directory", "capability_report",
)
# monthly_summary aggregates every lab's spend and the whole estate's downtime, so it is
# an admin template (spec 05: "generate_document admin templates" is T3).
ADMIN_ONLY_TEMPLATES = frozenset({"monthly_summary"})

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- shared helpers ---------------------------------------------------------------


def _parse_dt(value: str, field_name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise invalid_params(
            f"{field_name} is not a valid ISO-8601 timestamp.",
            "Use e.g. 2026-03-18T09:00:00Z.",
        ) from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _is_date_only(value: str) -> bool:
    """True for 2027-12-02, false for 2027-12-02T14:00 — a day, not an instant."""
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value).strip()))


def _check_month(month: str | None) -> str | None:
    if month is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}", str(month)):
        raise invalid_params("month must be formatted YYYY-MM.", "For example 2026-03.")
    return str(month)


def _resolve_target_user(ctx: Ctx, s, target_id: str) -> dict:
    """Fetch a user the caller is entitled to read, or raise an indistinguishable error.

    A caller who may not read the target gets exactly the same 'forbidden' error whether
    or not the target exists — otherwise the error itself is an existence oracle
    (spec 05 required test).
    """
    row = s.execute(
        text(
            """SELECT id, email, name, role, lab_id, training, account_codes
               FROM infinity.users WHERE id = :id"""
        ),
        {"id": target_id},
    ).mappings().first()

    if ctx.is_admin:
        if row is None:
            raise not_found("user")
        return dict(row)
    if target_id == ctx.user_id:
        if row is None:
            raise not_found("user")
        return dict(row)
    if ctx.is_pi and row is not None and row["lab_id"] in ctx.lab_ids:
        return dict(row)
    raise forbidden()


def _assert_can_read_lab(ctx: Ctx, lab_id: str) -> None:
    if ctx.is_admin:
        return
    if ctx.is_pi and lab_id in ctx.lab_ids:
        return
    raise forbidden()


def _owner_lab(s, user_id: str) -> str | None:
    return s.execute(
        text("SELECT lab_id FROM infinity.users WHERE id = :id"), {"id": user_id}
    ).scalar_one_or_none()


def _assert_can_read_owned_by(ctx: Ctx, s, owner_id: str) -> None:
    """Entitlement to a record owned by `owner_id`: self, own-lab PI, or admin."""
    if ctx.is_admin or owner_id == ctx.user_id:
        return
    if ctx.is_pi and _owner_lab(s, owner_id) in ctx.lab_ids:
        return
    raise forbidden()


def _caller_account_codes(s, ctx: Ctx) -> list[str]:
    codes = s.execute(
        text("SELECT account_codes FROM infinity.users WHERE id = :id"), {"id": ctx.user_id}
    ).scalar_one_or_none()
    return list(codes or [])


# --- 1. get_user_profile ----------------------------------------------------------


def get_user_profile(ctx: Ctx, user_id: str | None = None) -> dict[str, Any]:
    target = user_id or ctx.user_id
    with session_scope() as s:
        row = _resolve_target_user(ctx, s, target)
        lab_name = s.execute(
            text("SELECT name FROM infinity.labs WHERE id = :id"), {"id": row["lab_id"]}
        ).scalar_one_or_none()
    return {
        "user_id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "lab": {"id": row["lab_id"], "name": lab_name},
        "training": row["training"],
        "account_codes": list(row["account_codes"] or []),
    }


# --- 2. get_facility_catalog (T0) --------------------------------------------------


def get_facility_catalog(ctx: Ctx, facility_id: str | None = None) -> dict[str, Any]:
    with session_scope() as s:
        facilities = s.execute(
            text(
                """SELECT id, name, code FROM infinity.facilities
                   WHERE (CAST(:fid AS text) IS NULL OR id = :fid) ORDER BY name"""
            ),
            {"fid": facility_id},
        ).mappings().all()
        if facility_id and not facilities:
            raise not_found("facility")
        instruments = s.execute(
            text(
                """SELECT i.id, i.name, i.hourly_rate, i.status, i.facility_id, f.name AS facility
                   FROM infinity.instruments i
                   JOIN infinity.facilities f ON f.id = i.facility_id
                   WHERE (CAST(:fid AS text) IS NULL OR i.facility_id = :fid)
                   ORDER BY f.name, i.name"""
            ),
            {"fid": facility_id},
        ).mappings().all()
        templates = s.execute(
            text(
                """SELECT id, name, facility_id, fields FROM infinity.request_templates
                   WHERE (CAST(:fid AS text) IS NULL OR facility_id = :fid) ORDER BY name"""
            ),
            {"fid": facility_id},
        ).mappings().all()

    return {
        "facilities": [dict(f) for f in facilities],
        "instruments": [
            {**dict(i), "hourly_rate": float(i["hourly_rate"])} for i in instruments
        ],
        "templates": [dict(t) for t in templates],
    }


# --- 3. check_availability (T0) ----------------------------------------------------


def check_availability(
    ctx: Ctx, instrument_id: str, date_from: str, date_to: str
) -> dict[str, Any]:
    start = _parse_dt(date_from, "date_from")
    end = _parse_dt(date_to, "date_to")

    # "Is it free on Thursday?" is one day, and a planner asked for a single day writes
    # the same bare date twice. Rejecting that as "date_to must be after date_from" is
    # technically correct and useless: the caller asked a perfectly clear question. A
    # date with no time means the whole of that day.
    # A planner asked for one day writes the same value twice, and it is as likely to
    # spell it "2027-04-02T00:00:00Z" as "2027-04-02". Midnight to midnight is exactly
    # what a bare date expands to, so those are the same request and refusing one while
    # accepting the other turned "is LS7 free on 2 April?" into a lookup failure. A
    # repeated value carrying a real time (14:00 to 14:00) is still a mistake.
    midnight = start.astimezone(UTC).timetz().replace(tzinfo=None) == time(0, 0)
    if end == start and (midnight or (_is_date_only(date_from) and _is_date_only(date_to))):
        end = start + timedelta(days=1)

    if end <= start:
        raise invalid_params("The end of the window must be after the start.")
    if (end - start).days > 31:
        raise invalid_params("Window is limited to 31 days.", "Narrow the date range.")

    with session_scope() as s:
        instrument = s.execute(
            text(
                """SELECT i.id, i.name, i.status, i.hourly_rate, f.name AS facility
                   FROM infinity.instruments i
                   JOIN infinity.facilities f ON f.id = i.facility_id
                   WHERE i.id = :id"""
            ),
            {"id": instrument_id},
        ).mappings().first()
        if instrument is None:
            raise not_found("instrument")
        busy = s.execute(
            text(
                """SELECT starts_at, ends_at FROM infinity.bookings
                   WHERE instrument_id = :id AND status IN ('requested', 'confirmed')
                     AND starts_at < :end AND ends_at > :start
                   ORDER BY starts_at"""
            ),
            {"id": instrument_id, "start": start, "end": end},
        ).mappings().all()

    busy_intervals = [(b["starts_at"], b["ends_at"]) for b in busy]
    bookable = instrument["status"] == "available"
    free: list[dict[str, str]] = []
    day = start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        open_at = max(day.replace(hour=OPEN_HOUR), start)
        close_at = min(day.replace(hour=CLOSE_HOUR), end)
        cursor = open_at
        for b_start, b_end in busy_intervals:
            if b_end <= cursor or b_start >= close_at:
                continue
            if b_start > cursor:
                free.append({"starts_at": cursor.isoformat(), "ends_at": b_start.isoformat()})
            cursor = max(cursor, b_end)
        if cursor < close_at:
            free.append({"starts_at": cursor.isoformat(), "ends_at": close_at.isoformat()})
        day += timedelta(days=1)

    # A free slot is a gap in the *calendar*, and an instrument under maintenance has a
    # calendar full of them. Reporting those gaps is how "Light Sheet LS7 is currently
    # maintenance" became "Light Sheet LS7 is available" one turn later: the tool handed
    # the model a twelve-hour bookable-looking window for a machine that request_booking
    # rejects outright, and a wrong tool result becomes a confident wrong answer with an
    # evidence table under it. Nothing is free on an instrument nobody may book.
    if not bookable:
        free = []

    # Whether the *requested* window is free is the question actually being asked, so the
    # tool answers it rather than leaving interval arithmetic to the model.
    window_free = bookable and not any(
        b_start < end and b_end > start for b_start, b_end in busy_intervals
    )

    return {
        "instrument": {
            "id": instrument["id"],
            "name": instrument["name"],
            "status": instrument["status"],
            "facility": instrument["facility"],
            "hourly_rate": float(instrument["hourly_rate"]),
        },
        "instrument_name": instrument["name"],
        "requested_window": f"{start.isoformat()} to {end.isoformat()}",
        "requested_window_free": window_free,
        "bookable": bookable,
        # Stated positively so the answer can lead with the real blocker. Without it the
        # only signal that maintenance was the cause is free = False alongside
        # conflicts = 0, which reads as a contradiction rather than a reason.
        "unavailable_reason": (
            None if bookable
            else f"{instrument['name']} is {instrument['status']} and cannot be booked"
        ),
        "conflicting_bookings": len(busy),
        "window": {"from": start.isoformat(), "to": end.isoformat()},
        "opening_hours": f"{OPEN_HOUR:02d}:00-{CLOSE_HOUR:02d}:00 UTC",
        "busy": [
            {"starts_at": b["starts_at"].isoformat(), "ends_at": b["ends_at"].isoformat()}
            for b in busy
        ],
        "free_slots": free,
    }


# --- 4. get_my_bookings (T1) -------------------------------------------------------


def get_my_bookings(ctx: Ctx, date_from: str | None = None,
                    date_to: str | None = None) -> dict[str, Any]:
    start = _parse_dt(date_from, "date_from") if date_from else None
    end = _parse_dt(date_to, "date_to") if date_to else None
    with session_scope() as s:
        rows = s.execute(
            text(
                """SELECT b.id, i.name AS instrument, f.name AS facility,
                          b.starts_at, b.ends_at, b.status, b.account_code
                   FROM infinity.bookings b
                   JOIN infinity.instruments i ON i.id = b.instrument_id
                   JOIN infinity.facilities f  ON f.id = i.facility_id
                   WHERE b.user_id = :uid
                     AND (CAST(:start AS timestamptz) IS NULL OR b.ends_at   >= :start)
                     AND (CAST(:end AS timestamptz) IS NULL OR b.starts_at <= :end)
                   ORDER BY b.starts_at DESC"""
            ),
            {"uid": ctx.user_id, "start": start, "end": end},
        ).mappings().all()

    return {
        "user_id": ctx.user_id,
        "count": len(rows),
        "bookings": [
            {
                "id": r["id"],
                "instrument": r["instrument"],
                "facility": r["facility"],
                "starts_at": r["starts_at"].isoformat(),
                "ends_at": r["ends_at"].isoformat(),
                "status": r["status"],
                "account_code": r["account_code"],
            }
            for r in rows
        ],
    }


# --- 5. get_usage_records (T1/T2) --------------------------------------------------


def get_usage_records(ctx: Ctx, scope: str = "user", id: str | None = None,
                      month: str | None = None) -> dict[str, Any]:
    if scope not in ("user", "lab", "instrument"):
        raise invalid_params(
            "Usage can be read for a user, a lab or an instrument.",
            "Say which of those you mean.",
        )
    month = _check_month(month)

    with session_scope() as s:
        if scope == "user":
            target = id or ctx.user_id
            _resolve_target_user(ctx, s, target)
            where, params = "user_id = :target", {"target": target}
        elif scope == "lab":
            if not id:
                raise invalid_params(
                    "Usage for a lab needs to say which lab.",
                    "Name the lab, e.g. lab-a.",
                )
            _assert_can_read_lab(ctx, id)
            where, params = "lab_id = :target", {"target": id}
        else:
            # Instrument-wide usage exposes other people's hours: PI (own labs) or admin.
            if not id:
                raise invalid_params(
                    "Usage for an instrument needs to say which instrument.",
                    "Name the instrument, e.g. Confocal C2.",
                )
            if not (ctx.is_admin or ctx.is_pi):
                raise forbidden()
            where = "instrument = (SELECT name FROM infinity.instruments WHERE id = :target)"
            params = {"target": id}
            if ctx.is_pi:
                where += " AND lab_id = ANY(:labs)"
                params["labs"] = list(ctx.lab_ids)

        params["month"] = month
        rows = s.execute(
            text(
                f"""SELECT lab_id, user_id, instrument, month, scheduled_hours, tracked_hours
                    FROM reporting.v_usage_summary
                    WHERE {where} AND (CAST(:month AS text) IS NULL OR month = :month)
                    ORDER BY month DESC, instrument"""
            ),
            params,
        ).mappings().all()

    scheduled = sum(float(r["scheduled_hours"] or 0) for r in rows)
    tracked = sum(float(r["tracked_hours"] or 0) for r in rows)
    return {
        "scope": scope,
        "id": id or (ctx.user_id if scope == "user" else None),
        "month": month,
        "rows": [
            {**dict(r), "scheduled_hours": float(r["scheduled_hours"] or 0),
             "tracked_hours": float(r["tracked_hours"] or 0)}
            for r in rows
        ],
        "totals": {
            "scheduled_hours": round(scheduled, 2),
            "tracked_hours": round(tracked, 2),
            "difference_hours": round(tracked - scheduled, 2),
        },
    }


# --- 6. get_request_status (T1/T2) -------------------------------------------------


def get_request_status(ctx: Ctx, request_id: str | None = None,
                       mine: bool = False) -> dict[str, Any]:
    if not request_id and not mine:
        raise invalid_params("Say which request, or ask for your own.")

    with session_scope() as s:
        if request_id:
            row = s.execute(
                text(
                    """SELECT sr.id, sr.user_id, sr.template_id, t.name AS template,
                              sr.fields, sr.status, sr.history
                       FROM infinity.service_requests sr
                       JOIN infinity.request_templates t ON t.id = sr.template_id
                       WHERE sr.id = :id"""
                ),
                {"id": request_id},
            ).mappings().first()
            # Same indistinguishability rule as user profiles.
            if row is None:
                raise forbidden() if not ctx.is_admin else not_found("request")
            _assert_can_read_owned_by(ctx, s, row["user_id"])
            samples = s.execute(
                text(
                    """SELECT barcode, state, updated_at FROM infinity.samples
                       WHERE request_id = :id ORDER BY barcode"""
                ),
                {"id": request_id},
            ).mappings().all()
            return {
                "request": {
                    **{k: row[k] for k in ("id", "user_id", "template_id", "template",
                                           "fields", "status", "history")},
                    "samples": [
                        {"barcode": s_["barcode"], "state": s_["state"],
                         "updated_at": s_["updated_at"].isoformat()}
                        for s_ in samples
                    ],
                }
            }

        rows = s.execute(
            text(
                """SELECT sr.id, sr.template_id, t.name AS template, sr.status, sr.history
                   FROM infinity.service_requests sr
                   JOIN infinity.request_templates t ON t.id = sr.template_id
                   WHERE sr.user_id = :uid
                   ORDER BY sr.id DESC"""
            ),
            {"uid": ctx.user_id},
        ).mappings().all()

    return {"user_id": ctx.user_id, "count": len(rows), "requests": [dict(r) for r in rows]}


# --- 7. track_sample (T1/T2) -------------------------------------------------------


def track_sample(ctx: Ctx, barcode: str | None = None,
                 sample_id: str | None = None) -> dict[str, Any]:
    if not barcode and not sample_id:
        raise invalid_params("Give either the barcode or the sample id.")

    with session_scope() as s:
        row = s.execute(
            text(
                """SELECT s.id, s.barcode, s.state, s.updated_at, s.request_id,
                          sr.user_id, sr.status AS request_status, sr.history,
                          t.name AS template
                   FROM infinity.samples s
                   JOIN infinity.service_requests sr ON sr.id = s.request_id
                   JOIN infinity.request_templates t ON t.id = sr.template_id
                   WHERE (CAST(:bc AS text) IS NOT NULL AND s.barcode = :bc)
                      OR (CAST(:sid AS text) IS NOT NULL AND s.id = :sid)"""
            ),
            {"bc": barcode, "sid": sample_id},
        ).mappings().first()
        if row is None:
            raise forbidden() if not ctx.is_admin else not_found("sample")
        _assert_can_read_owned_by(ctx, s, row["user_id"])

    history = row["history"] or []
    timeline = [
        {"at": h.get("at"), "event": f"request {h.get('status')}", "by": h.get("by")}
        for h in history
    ]
    timeline.append({
        "at": row["updated_at"].isoformat(),
        "event": f"sample {row['state']}",
        "by": None,
    })
    return {
        "sample": {
            "id": row["id"],
            "barcode": row["barcode"],
            "state": row["state"],
            "updated_at": row["updated_at"].isoformat(),
            "request_id": row["request_id"],
            "request_status": row["request_status"],
            "template": row["template"],
        },
        "timeline": sorted(timeline, key=lambda e: str(e["at"])),
    }


# --- 8. get_billing_summary (T1 own codes / T2 / T3) -------------------------------


def get_billing_summary(ctx: Ctx, account_code: str, period: str) -> dict[str, Any]:
    if not re.fullmatch(r"\d{4}-\d{2}", str(period)):
        raise invalid_params("period must be formatted YYYY-MM.", "For example 2026-03.")

    with session_scope() as s:
        code_row = s.execute(
            text("SELECT code, lab_id FROM infinity.account_codes WHERE code = :c"),
            {"c": account_code},
        ).mappings().first()

        if ctx.is_admin:
            if code_row is None:
                raise not_found("account code")
        elif ctx.is_pi:
            if code_row is None or code_row["lab_id"] not in ctx.lab_ids:
                raise forbidden()
        else:
            if account_code not in _caller_account_codes(s, ctx):
                raise forbidden()

        invoice = s.execute(
            text(
                """SELECT id, total FROM infinity.invoices
                   WHERE account_code = :c AND period = :p"""
            ),
            {"c": account_code, "p": period},
        ).mappings().first()
        lines = s.execute(
            text(
                """SELECT description, instrument, amount FROM reporting.v_billing_lines
                   WHERE account_code = :c AND period = :p
                   ORDER BY amount DESC"""
            ),
            {"c": account_code, "p": period},
        ).mappings().all()

    # Money stays Decimal: casting to float both loses the record's own spelling
    # (2689.00 becomes 2689.0, which then reaches the user) and is the wrong type for
    # currency. Serializers render Decimal fine.
    return {
        "account_code": account_code,
        "period": period,
        "lab_id": code_row["lab_id"] if code_row else None,
        "invoice_id": invoice["id"] if invoice else None,
        "total": invoice["total"] if invoice else Decimal("0.00"),
        "line_count": len(lines),
        "lines": [
            {"description": ln["description"], "instrument": ln["instrument"],
             "amount": ln["amount"]}
            for ln in lines
        ],
    }


# --- 9. get_project_overview (T2 member/pi / T3) -----------------------------------


def get_project_overview(ctx: Ctx, project_id: str) -> dict[str, Any]:
    with session_scope() as s:
        project = s.execute(
            text("SELECT id, name, currency FROM infinity.projects WHERE id = :id"),
            {"id": project_id},
        ).mappings().first()

        if not (ctx.is_admin or ctx.is_pi):
            raise forbidden()
        if project is None:
            raise forbidden() if not ctx.is_admin else not_found("project")

        members = s.execute(
            text(
                """SELECT pm.user_id, pm.role, u.name, u.lab_id
                   FROM infinity.project_members pm
                   JOIN infinity.users u ON u.id = pm.user_id
                   WHERE pm.project_id = :id
                   ORDER BY pm.role, u.name"""
            ),
            {"id": project_id},
        ).mappings().all()

        member_labs = {m["lab_id"] for m in members if m["lab_id"]}
        if ctx.is_pi and not (member_labs & set(ctx.lab_ids)):
            raise forbidden()

        visible_labs = member_labs if ctx.is_admin else member_labs & set(ctx.lab_ids)
        # No project<->account-code link exists in Infinity X, so project spend is
        # attributed through the labs of its members — stated in the response so the
        # number is never presented as something it is not.
        spend = s.execute(
            text(
                """SELECT lab_id, period, sum(amount) AS total
                   FROM reporting.v_billing_lines
                   WHERE lab_id = ANY(:labs)
                   GROUP BY lab_id, period ORDER BY period, lab_id"""
            ),
            {"labs": list(visible_labs) or [""]},
        ).mappings().all()
        cores = s.execute(
            text(
                """SELECT DISTINCT f.name AS facility
                   FROM infinity.bookings b
                   JOIN infinity.users u       ON u.id = b.user_id
                   JOIN infinity.instruments i ON i.id = b.instrument_id
                   JOIN infinity.facilities f  ON f.id = i.facility_id
                   WHERE u.id = ANY(:members) ORDER BY f.name"""
            ),
            {"members": [m["user_id"] for m in members] or [""]},
        ).scalars().all()

    return {
        "project": dict(project),
        "members": [dict(m) for m in members],
        "cores_used": list(cores),
        "spend_by_lab_period": [
            {"lab_id": r["lab_id"], "period": r["period"], "total": float(r["total"])}
            for r in spend
        ],
        "spend_total": round(sum(float(r["total"]) for r in spend), 2),
        "spend_basis": (
            "Attributed via the labs of project members "
            f"({', '.join(sorted(visible_labs)) or 'none visible to you'}); "
            "Infinity X does not bill projects directly."
        ),
    }


# --- 10. get_instrument_health (status T0 / history T3) ----------------------------


def get_instrument_health(ctx: Ctx, instrument_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.execute(
            text(
                """SELECT i.id, i.name, i.status, i.hourly_rate, f.name AS facility
                   FROM infinity.instruments i
                   JOIN infinity.facilities f ON f.id = i.facility_id
                   WHERE i.id = :id"""
            ),
            {"id": instrument_id},
        ).mappings().first()
        if row is None:
            raise not_found("instrument")

        out: dict[str, Any] = {
            "instrument": {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "facility": row["facility"],
                "hourly_rate": float(row["hourly_rate"]),
            }
        }
        if not ctx.is_admin:
            out["note"] = "Maintenance history and downtime are available to admins."
            return out

        events = s.execute(
            text(
                """SELECT kind, notes, occurred_at, downtime_hours
                   FROM infinity.maintenance_events
                   WHERE instrument_id = :id ORDER BY occurred_at DESC"""
            ),
            {"id": instrument_id},
        ).mappings().all()

    out["history"] = [
        {"kind": e["kind"], "notes": e["notes"], "occurred_at": e["occurred_at"].isoformat(),
         "downtime_hours": float(e["downtime_hours"])}
        for e in events
    ]
    out["downtime_hours_total"] = round(sum(float(e["downtime_hours"]) for e in events), 2)
    out["repair_count"] = sum(1 for e in events if e["kind"] == "repair")
    return out


# --- 11. run_readonly_sql (T2/T3) --------------------------------------------------


def run_readonly_sql(ctx: Ctx, sql: str) -> dict[str, Any]:
    if not (ctx.is_admin or ctx.is_pi):
        raise forbidden()

    validated = validate_sql(sql, ctx.role, ctx.lab_ids)

    try:
        with ro_session() as s:
            result = s.execute(text(validated.executed_sql))
            columns = list(result.keys())
            rows = [dict(zip(columns, r, strict=True)) for r in result.fetchall()]
    except SQLAlchemyError as exc:
        # Validated but still not runnable (unknown column, bad cast, timeout). Surface
        # it as the uniform error so the caller can repair rather than crash the turn.
        detail = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
        log.info("run_readonly_sql execution failed: %s", detail)
        raise ToolError(
            "sql_rejected",
            f"The query was valid but could not run: {detail}",
            "Check the column names against the four reporting views.",
        ) from exc

    log.info(
        "run_readonly_sql caller=%s sql_in=%r sql_executed=%r rows=%d",
        ctx.user_id, sql, validated.executed_sql, len(rows),
    )
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "executed_sql": validated.executed_sql,
        "truncated": len(rows) >= MAX_ROWS,
        "lab_filtered": validated.lab_filtered,
    }


# --- 12. create_onboarding_request (write) -----------------------------------------


def create_onboarding_request(ctx: Ctx, name: str, email: str, lab_id: str,
                              pi_ack: bool, account_code: str | None = None) -> dict[str, Any]:
    if not name or not str(name).strip():
        raise invalid_params("name is required.")
    if not EMAIL_RE.fullmatch(str(email or "")):
        raise invalid_params("email is not a valid address.")
    if not pi_ack:
        raise invalid_params(
            "pi_ack must be true.",
            "The PI must acknowledge the new user before onboarding can be proposed.",
        )
    # A user may onboard someone into their own lab; admins into any lab.
    if not ctx.is_admin and lab_id not in ctx.lab_ids:
        raise forbidden()

    with session_scope() as s:
        if not s.execute(
            text("SELECT 1 FROM infinity.labs WHERE id = :id"), {"id": lab_id}
        ).first():
            raise not_found("lab")
        if account_code:
            code_lab = s.execute(
                text("SELECT lab_id FROM infinity.account_codes WHERE code = :c"),
                {"c": account_code},
            ).scalar_one_or_none()
            if code_lab is None:
                raise not_found("account code")
            if code_lab != lab_id:
                raise invalid_params("That account code belongs to a different lab.")
        if s.execute(
            text("SELECT 1 FROM infinity.users WHERE email = :e"), {"e": email}
        ).first():
            raise ToolError("conflict", "A user with that email already exists.", "")

    payload = {"name": name, "email": email, "lab_id": lab_id,
               "pi_ack": bool(pi_ack), "account_code": account_code}
    preview = (
        f"Onboard {name} <{email}> into {lab_id}"
        + (f" on account {account_code}" if account_code else "")
    )
    return actions_mod.create_pending(ctx, "create_onboarding_request", payload, preview)


# --- 13. create_service_request (write) --------------------------------------------


def create_service_request(ctx: Ctx, template_id: str,
                           fields: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = fields or {}
    if not isinstance(fields, dict):
        raise invalid_params("fields must be an object.")

    with session_scope() as s:
        tpl = s.execute(
            text("SELECT id, name, fields FROM infinity.request_templates WHERE id = :id"),
            {"id": template_id},
        ).mappings().first()
        if tpl is None:
            raise not_found("template")

    missing = [
        f["name"] for f in tpl["fields"]
        if f.get("required") and fields.get(f["name"]) in (None, "")
    ]
    if missing:
        raise invalid_params(
            f"Missing required field(s): {', '.join(missing)}.",
            f"Template '{tpl['name']}' requires: "
            + ", ".join(f["name"] for f in tpl["fields"] if f.get("required"))
            + ".",
        )
    for f in tpl["fields"]:
        value = fields.get(f["name"])
        if value is None:
            continue
        if f.get("type") == "enum" and value not in f.get("options", []):
            raise invalid_params(
                f"{f['name']} must be one of: {', '.join(f.get('options', []))}."
            )
        if f.get("type") == "integer" and not isinstance(value, int):
            raise invalid_params(f"{f['name']} must be an integer.")

    payload = {"template_id": template_id, "fields": fields}
    preview = f"Submit '{tpl['name']}' with " + ", ".join(f"{k}={v}" for k, v in fields.items())
    return actions_mod.create_pending(ctx, "create_service_request", payload, preview)


# --- 14. request_booking (write) ---------------------------------------------------


def request_booking(ctx: Ctx, instrument_id: str, starts_at: str, ends_at: str,
                    account_code: str | None = None) -> dict[str, Any]:
    # Fall back to the code this caller last had a booking approved on. It is a
    # convenience, not an assumption: the value appears on the approval card, so a wrong
    # guess costs one click. Entitlement is still checked below, exactly as if it had
    # been typed.
    if not account_code:
        account_code = memory.recall(ctx.user_id).get(memory.ACCOUNT_CODE)
        if not account_code:
            raise invalid_params(
                "account_code is required.",
                "Say which account code to charge, e.g. ACC-A1.",
            )
        log.info("using remembered account code %s for %s", account_code, ctx.user_id)
    start = _parse_dt(starts_at, "starts_at")
    end = _parse_dt(ends_at, "ends_at")
    if end <= start:
        raise invalid_params("The end of the booking must be after its start.")
    if (end - start) > timedelta(hours=12):
        raise invalid_params("A single booking may not exceed 12 hours.")

    # check_availability publishes 08:00-20:00 and computes its free slots inside those
    # hours; this tool accepted 03:00 anyway. Two tools disagreeing about the same rule is
    # the bug — a caller who books exactly what availability offered is fine, and one who
    # asks for the small hours is told the rule rather than silently given a slot the
    # facility does not honour.
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    if start_utc.date() != (end_utc - timedelta(microseconds=1)).date():
        raise invalid_params(
            "A booking must start and finish on the same day.",
            "Book each day separately.",
        )
    open_at = start_utc.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
    close_at = start_utc.replace(hour=CLOSE_HOUR, minute=0, second=0, microsecond=0)
    if start_utc < open_at or end_utc > close_at:
        raise invalid_params(
            f"Bookings must fall within opening hours "
            f"({OPEN_HOUR:02d}:00-{CLOSE_HOUR:02d}:00 UTC).",
            f"Pick a slot between {OPEN_HOUR:02d}:00 and {CLOSE_HOUR:02d}:00 UTC.",
        )

    with session_scope() as s:
        instrument = s.execute(
            text("SELECT id, name, status FROM infinity.instruments WHERE id = :id"),
            {"id": instrument_id},
        ).mappings().first()
        if instrument is None:
            raise not_found("instrument")
        if instrument["status"] != "available":
            raise ToolError(
                "conflict",
                f"{instrument['name']} is currently {instrument['status']}.",
                "Pick another instrument or check back after maintenance.",
            )

        code_lab = s.execute(
            text("SELECT lab_id FROM infinity.account_codes WHERE code = :c"),
            {"c": account_code},
        ).scalar_one_or_none()
        if code_lab is None:
            raise not_found("account code")
        if ctx.is_admin:
            pass
        elif ctx.is_pi:
            if code_lab not in ctx.lab_ids:
                raise forbidden()
        elif account_code not in _caller_account_codes(s, ctx):
            raise forbidden()

        clash = s.execute(
            text(
                """SELECT id FROM infinity.bookings
                   WHERE instrument_id = :iid AND status IN ('requested', 'confirmed')
                     AND starts_at < :end AND ends_at > :start LIMIT 1"""
            ),
            {"iid": instrument_id, "start": start, "end": end},
        ).first()
        if clash:
            raise ToolError(
                "conflict",
                "That slot overlaps an existing booking.",
                "Use check_availability to find a free slot.",
            )

    payload = {
        "instrument_id": instrument_id,
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "account_code": account_code,
    }
    hours = (end - start).total_seconds() / 3600
    preview = (
        f"Book {instrument['name']} for {hours:.1f} h on {start.date()} "
        f"({start.strftime('%H:%M')}–{end.strftime('%H:%M')} UTC), account {account_code}"
    )
    return actions_mod.create_pending(ctx, "request_booking", payload, preview)


# --- 15. generate_document (write) -------------------------------------------------


def generate_document(ctx: Ctx, template: str,
                      params: dict[str, Any] | None = None,
                      format: str = "md") -> dict[str, Any]:
    params = params or {}
    fmt = str(format).lower()
    if fmt not in documents.FORMATS:
        raise invalid_params(
            f"format must be one of: {', '.join(documents.FORMATS)}."
        )
    if template not in DOCUMENT_TEMPLATES:
        raise invalid_params(
            f"template must be one of: {', '.join(DOCUMENT_TEMPLATES)}."
        )
    if template in ADMIN_ONLY_TEMPLATES and not ctx.is_admin:
        raise forbidden()

    if template == "usage_report":
        target = params.get("user_id") or ctx.user_id
        with session_scope() as s:
            _resolve_target_user(ctx, s, target)
        params = {**params, "user_id": target}
        _check_month(params.get("month"))
    elif template == "onboarding_packet":
        lab_id = params.get("lab_id")
        if lab_id and not ctx.is_admin and lab_id not in ctx.lab_ids:
            raise forbidden()
    elif template == "monthly_summary":
        _check_month(params.get("period"))

    payload = {"template": template, "params": params, "format": fmt}
    # The approval card is the last thing a human reads before this happens, so it is
    # written for them: a repr'd dict put `{'account_code': 'ACC-A1', 'lab_id': ...}` on
    # screen, which is the schema talking, not the proposal.
    detail = (
        ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(params.items()))
        if params else "no parameters"
    )
    preview = f"Generate {template.replace('_', ' ')} as {fmt.upper()} ({detail})"
    return actions_mod.create_pending(ctx, "generate_document", payload, preview)


# --- discovery: shared machinery for tools 16 and 17 -------------------------------

EARTH_RADIUS_KM = 6371.0

# Words that carry no capability signal. "I want to do cryo-EM" and "cryo-EM" must score
# the same instrument identically, so the framing a person puts around their goal is
# removed before anything is compared.
_GOAL_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "on", "in", "at", "with", "from",
    "my", "our", "me", "we", "i", "it", "is", "are", "be", "do", "does", "did", "want",
    "wants", "need", "needs", "would", "like", "looking", "look", "use", "using", "run",
    "get", "some", "any", "best", "which", "what", "where", "how", "can", "could",
    "should", "please", "help", "instrument", "machine", "kit", "book", "booking",
})

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

# Points per piece of evidence. An instrument whose recorded technique is literally what
# the caller asked for is the answer; everything else is corroboration, so the exact
# match has to outweigh any amount of incidental word overlap.
_TECHNIQUE_EXACT_POINTS = 10
_TOKEN_POINTS = {"techniques": 3, "modality": 2, "sample types": 2, "specification": 1}


def _normalise(value: str | None) -> str:
    """Free text as lowercase words separated by single spaces.

    'Cryo-EM', 'cryo EM' and 'cryo-em' are one request spelled three ways, and a search
    that distinguishes them answers "nothing here does that" about a machine that does.
    """
    return _NON_WORD_RE.sub(" ", str(value or "").lower()).strip()


def _singular(word: str) -> str:
    """Fold a trailing plural 's'.

    'live cells' (what a user types) and 'live-cell imaging' (what the catalogue records)
    are the same capability, and matching them as different strings loses the instrument
    that does exactly what was asked. Both sides pass through here, so this only has to be
    consistent — not linguistically correct.
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(value: str | None) -> set[str]:
    """The meaningful words in a phrase, singularised, for overlap scoring."""
    return {
        _singular(word)
        for word in _normalise(value).split()
        if len(word) > 1 and word not in _GOAL_STOPWORDS
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two points in decimal degrees.

    Exact to within metres at campus range, which is the only range this platform has.
    Written out rather than imported: it is six lines of arithmetic, and the alternative
    is carrying a geospatial dependency for three sites whose coordinates are known.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def _coordinate(value: Any, name: str, limit: float) -> float:
    """A coordinate the caller supplied, or a typed refusal — never a silent 0.0."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise invalid_params(
            f"{name} must be a number in decimal degrees.",
            "For example 51.5243 and -0.1339.",
        ) from exc
    if not -limit <= number <= limit:
        raise invalid_params(f"{name} must be between -{limit:g} and {limit:g} degrees.")
    return number


# The card contract knows four tones; only 'available' means bookable, so anything else is
# a warning. Defaulting an unrecognised status to a neutral tone is how a machine nobody
# may touch would come to look bookable on screen.
def _status_tone(status: str | None) -> str:
    return "ok" if str(status or "").lower() == "available" else "warn"


def _card_field(label: str, value: Any, emphasis: bool = False) -> dict[str, Any]:
    return {"label": label, "value": str(value), "emphasis": bool(emphasis)}


def _instrument_row(row) -> dict[str, Any]:
    """One instrument as the discovery tools report it, values exactly as stored."""
    return {
        "id": row["id"],
        "name": row["name"],
        "hourly_rate": float(row["hourly_rate"]),
        "status": row["status"],
        # Stated positively so a caller is never left to infer bookability from a status
        # string it may not recognise — the same contract check_availability publishes.
        "bookable": row["status"] == "available",
        "modality": row["modality"],
        "techniques": list(row["techniques"] or []),
        "sample_types": list(row["sample_types"] or []),
        "specification": row["specification"],
        "room": row["room"],
    }


def _matches_technique(row, technique: str) -> bool:
    """True when this instrument's techniques or modality contain `technique` as text.

    Matched in Python rather than with ILIKE on purpose: the needle is free text a user
    typed, and a '%' or '_' in it would quietly become a wildcard — "100_bp" would match
    everything and the caller would never know the question they asked was not the
    question that ran.
    """
    probe = _normalise(technique)
    if not probe:
        # A search term with nothing searchable in it ('%', '---') matches nothing. The
        # tempting alternative — treating it as no filter — is the same wildcard bug by
        # another route: a typo comes back as the entire directory under the heading
        # "facilities that do %".
        return False
    haystacks = [_normalise(t) for t in (row["techniques"] or [])]
    haystacks.append(_normalise(row["modality"]))
    return any(probe in hay for hay in haystacks if hay)


# --- 16. find_facilities (T0) ------------------------------------------------------


def _facilities_card(
    facilities: list[dict[str, Any]], technique: str | None, campus: str | None,
    located: bool,
) -> dict[str, Any]:
    """The facilities card. Every value here is copied from the rows above it."""
    fields = []
    if technique:
        fields.append(_card_field("Technique", technique))
    if campus:
        fields.append(_card_field("Campus", campus))
    fields.append(_card_field("Facilities", len(facilities), emphasis=True))
    fields.append(
        _card_field("Instruments", sum(len(f["instruments"]) for f in facilities))
    )
    if located and facilities and facilities[0].get("distance_km") is not None:
        nearest = facilities[0]
        fields.append(
            _card_field("Nearest", f"{nearest['name']} — {nearest['distance_km']} km", True)
        )

    items = []
    for f in facilities:
        count = len(f["instruments"])
        # `value` is the figure the UI sets apart, so on a "where is the nearest core"
        # answer it has to be the distance — that is the whole question. The instrument
        # count is context and belongs with the rest of the detail in meta. Without a
        # distance (no origin given) the count takes the slot back rather than leaving a
        # conspicuous gap.
        meta = [f"{count} instrument" + ("" if count == 1 else "s")]
        meta += [str(v) for v in (f["room"], f["address"], f["opening_hours"],
                                  f["contact_email"]) if v]
        distance = f.get("distance_km")
        items.append({
            "title": f["name"],
            "subtitle": " · ".join(str(v) for v in (f["campus"], f["building"]) if v) or None,
            "meta": meta,
            "badges": [
                {"text": f"{i['name']} · {i['status']}", "tone": _status_tone(i["status"])}
                for i in f["instruments"]
            ],
            "value": f"{distance} km" if distance is not None else meta[0],
        })

    return {
        "kind": "facilities",
        "title": f"Facilities that do {technique}" if technique else "Facility directory",
        "subtitle": "Nearest first" if located else None,
        "fields": fields,
        "items": items,
        # An empty card must say why it is empty. Without this the UI renders a blank
        # panel, which a reader completes for themselves — usually as "the system is
        # broken" and sometimes as "there is nothing anywhere".
        "footer": (
            f"No instrument on record lists '{technique}'." if technique and not items
            else None
        ),
    }


def find_facilities(ctx: Ctx, technique: str | None = None,
                    near_latitude: float | None = None,
                    near_longitude: float | None = None,
                    campus: str | None = None) -> dict[str, Any]:
    """Facilities that can do `technique`, each carrying the instruments that can do it.

    Guarantees:
      * A facility is returned only if at least one of its instruments matches, so an
        empty list means "nothing on record does that" and never "here is everything".
        Falling back to the full directory would read as "these can do it", which is a
        fabricated capability claim wearing the clothes of a search result.
      * With a location, every facility carries `distance_km` (haversine, 2dp) and the
        list runs nearest first. A facility with no coordinates on file gets None and
        sorts last rather than being dropped or given a made-up distance.
      * `matched` is the number of facilities returned; `matched_instruments` the number
        of instruments behind them.

    T0: a facility directory is public information. There is no per-caller filter here —
    but ctx is still required, so there is no anonymous access either.
    """
    if (near_latitude is None) != (near_longitude is None):
        raise invalid_params(
            "A location needs both a latitude and a longitude.",
            "Give both, e.g. near_latitude 51.5243 and near_longitude -0.1339.",
        )
    # A blank technique is the absence of a filter, not a filter that matches nothing:
    # planners spell "no technique given" as both None and "". Everything downstream then
    # has one thing to test.
    technique = str(technique).strip() if str(technique or "").strip() else None
    campus = str(campus).strip() if str(campus or "").strip() else None

    origin: tuple[float, float] | None = None
    if near_latitude is not None:
        origin = (
            _coordinate(near_latitude, "near_latitude", 90.0),
            _coordinate(near_longitude, "near_longitude", 180.0),
        )

    with session_scope() as s:
        facility_rows = s.execute(
            text(
                """SELECT id, name, code, campus, building, room, address,
                          latitude, longitude, contact_email, opening_hours
                   FROM infinity.facilities ORDER BY name"""
            )
        ).mappings().all()
        instrument_rows = s.execute(
            text(
                """SELECT id, facility_id, name, hourly_rate, status, modality,
                          techniques, sample_types, specification, room
                   FROM infinity.instruments ORDER BY name"""
            )
        ).mappings().all()

    by_facility: dict[str, list[dict[str, Any]]] = {}
    for row in instrument_rows:
        if technique and not _matches_technique(row, technique):
            continue
        by_facility.setdefault(row["facility_id"], []).append(_instrument_row(row))

    wanted_campus = _normalise(campus)
    facilities: list[dict[str, Any]] = []
    for f in facility_rows:
        if wanted_campus and wanted_campus not in _normalise(f["campus"]):
            continue
        instruments = by_facility.get(f["id"], [])
        if technique and not instruments:
            continue
        entry: dict[str, Any] = {
            "id": f["id"],
            "name": f["name"],
            "code": f["code"],
            "campus": f["campus"],
            "building": f["building"],
            "room": f["room"],
            "address": f["address"],
            "contact_email": f["contact_email"],
            "opening_hours": f["opening_hours"],
            "latitude": float(f["latitude"]) if f["latitude"] is not None else None,
            "longitude": float(f["longitude"]) if f["longitude"] is not None else None,
            "instruments": instruments,
        }
        if origin:
            entry["distance_km"] = (
                None if entry["latitude"] is None or entry["longitude"] is None
                else round(_haversine_km(*origin, entry["latitude"], entry["longitude"]), 2)
            )
        facilities.append(entry)

    if origin:
        # None sorts last: "we do not know where this is" is not "this is zero km away".
        facilities.sort(
            key=lambda f: (f["distance_km"] is None, f["distance_km"] or 0.0, f["name"])
        )

    return {
        "technique": technique,
        "campus": campus,
        "origin": ({"latitude": origin[0], "longitude": origin[1]} if origin else None),
        "matched": len(facilities),
        "matched_instruments": sum(len(f["instruments"]) for f in facilities),
        "facilities": facilities,
        "card": _facilities_card(facilities, technique, campus, located=bool(origin)),
    }


# --- 17. recommend_instrument (T0) -------------------------------------------------


def _score_instrument(row, goal_text: str, goal_tokens: set[str]) -> tuple[int, list[str]]:
    """Score one instrument against a goal, and say what earned every point.

    Deterministic by construction — no model is consulted. A ranking that answers "which
    instrument?" has to be the same ranking tomorrow: this repo has been burned three
    times by prompt tweaks that regressed a passing case, and a recommendation that moves
    when a sentence is reworded cannot be tested, explained or defended to a scientist
    who is about to spend £145 an hour on the result.

    Returns (score, why_matched) where why_matched names the evidence in the caller's own
    words, so a recommendation is never a number with nothing behind it.
    """
    score = 0
    why: list[str] = []

    # An exact technique match is the answer, not a hint: it means the catalogue records
    # this machine as doing the literal thing that was asked for.
    exact_tokens: set[str] = set()
    for technique in row["techniques"] or []:
        normalised = _normalise(technique)
        if normalised and normalised in goal_text:
            score += _TECHNIQUE_EXACT_POINTS
            why.append(f"exact technique match: {technique}")
            exact_tokens |= _tokens(technique)

    for label, values in (
        ("techniques", list(row["techniques"] or [])),
        ("modality", [row["modality"]]),
        ("sample types", list(row["sample_types"] or [])),
        ("specification", [row["specification"]]),
    ):
        field_tokens: set[str] = set()
        for value in values:
            field_tokens |= _tokens(value)
        # Tokens already paid for by an exact match are not counted twice; the evidence
        # line would otherwise repeat back the words of the match above it.
        hits = sorted((field_tokens & goal_tokens) - exact_tokens)
        if hits:
            score += _TOKEN_POINTS[label] * len(hits)
            why.append(f"{label} match: {', '.join(hits)}")

    return score, why


def _accepts_sample(row, sample_type: str) -> bool:
    """True when this instrument's recorded sample types cover `sample_type`."""
    probe = _normalise(sample_type)
    if not probe:
        return True
    return any(
        probe in stored or stored in probe
        for stored in (_normalise(v) for v in (row["sample_types"] or []))
        if stored
    )


def _instruments_card(matches: list[dict[str, Any]], goal: str,
                      sample_type: str | None) -> dict[str, Any]:
    """The instruments card. Every value here is copied from the ranked rows."""
    fields = [_card_field("Goal", goal)]
    if sample_type:
        fields.append(_card_field("Sample type", sample_type))
    fields.append(_card_field("Matches", len(matches), emphasis=True))
    if matches:
        fields.append(_card_field("Best match", matches[0]["instrument"], emphasis=True))

    items = []
    for m in matches:
        meta = [f"${m['hourly_rate']:.2f}/h"]
        meta += [str(v) for v in (m["campus"], m["building"], m["room"]) if v]
        meta += m["why_matched"]
        badges = [{"text": m["status"], "tone": _status_tone(m["status"])}]
        if m["modality"]:
            badges.append({"text": m["modality"], "tone": "info"})
        items.append({
            "title": m["instrument"],
            "subtitle": m["facility"],
            "meta": meta,
            "badges": badges,
            # The score is published rather than hidden: a ranking a reader cannot audit
            # is indistinguishable from a guess, which is the one thing this must not be.
            "value": f"score {m['score']}",
        })

    return {
        "kind": "instruments",
        "title": "Recommended instruments",
        "subtitle": goal,
        "fields": fields,
        "items": items,
        "footer": (
            "Nothing on record matches that goal." if not matches
            else "Ranked by recorded capability, not by availability — check each status."
        ),
    }


def recommend_instrument(ctx: Ctx, goal: str,
                         sample_type: str | None = None) -> dict[str, Any]:
    """Instruments that can do what `goal` describes, best first, with the evidence.

    Guarantees:
      * Ranking is deterministic token overlap against the recorded techniques, modality,
        sample types and specification. No model is asked to rank anything.
      * Every match carries `why_matched` — the tokens and techniques that earned it its
        place — so the caller can see the reasoning rather than trust it.
      * An instrument that is not 'available' is still returned, carrying its status and
        `bookable: false`. Hiding it answers a different question ("what can I book right
        now?") than the one asked ("which instrument does this?") and leaves a scientist
        believing the capability does not exist here at all.
      * Nothing that scores zero is returned. An empty list is the honest answer to a goal
        this facility cannot serve.

    T0: this is the public capability catalogue, the same data as get_facility_catalog.
    """
    if not str(goal or "").strip():
        raise invalid_params(
            "Say what you want to do.",
            "For example 'cryo-EM of a protein complex' or 'image live cells'.",
        )

    goal_text = _normalise(goal)
    goal_tokens = _tokens(goal)

    with session_scope() as s:
        rows = s.execute(
            text(
                """SELECT i.id, i.name, i.hourly_rate, i.status, i.modality, i.techniques,
                          i.sample_types, i.specification, i.room,
                          f.id AS facility_id, f.name AS facility, f.campus, f.building,
                          f.contact_email, f.opening_hours
                   FROM infinity.instruments i
                   JOIN infinity.facilities f ON f.id = i.facility_id
                   ORDER BY i.name"""
            )
        ).mappings().all()

    matches: list[dict[str, Any]] = []
    excluded = 0
    for row in rows:
        score, why = _score_instrument(row, goal_text, goal_tokens)
        if score <= 0:
            continue
        # A sample type the instrument does not take is a different answer to a different
        # question, so it narrows the list rather than reordering it. The count of what it
        # removed is reported, because a filter that silently empties a result is a filter
        # nobody can argue with.
        if sample_type and not _accepts_sample(row, sample_type):
            excluded += 1
            continue
        matches.append({
            "instrument_id": row["id"],
            "instrument": row["name"],
            "facility_id": row["facility_id"],
            "facility": row["facility"],
            "campus": row["campus"],
            "building": row["building"],
            "room": row["room"],
            "hourly_rate": float(row["hourly_rate"]),
            "status": row["status"],
            "bookable": row["status"] == "available",
            "modality": row["modality"],
            "techniques": list(row["techniques"] or []),
            "sample_types": list(row["sample_types"] or []),
            "specification": row["specification"],
            "contact_email": row["contact_email"],
            "score": score,
            "why_matched": why,
        })

    # Equal evidence, so the tie-break is what the caller can act on: something they may
    # book today outranks something equally suitable that is in pieces on a bench. Name
    # last, so the order never depends on the order rows came back.
    matches.sort(key=lambda m: (-m["score"], not m["bookable"], m["instrument"]))

    return {
        "goal": goal,
        "sample_type": sample_type,
        "matched": len(matches),
        "excluded_by_sample_type": excluded,
        "matches": matches,
        "card": _instruments_card(matches, goal, sample_type),
    }


# --- registry ----------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    number: int
    name: str
    handler: Callable[..., dict]
    tier: str
    write: bool
    description: str
    params: dict[str, str] = field(default_factory=dict)


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in [
        ToolSpec(1, "get_user_profile", get_user_profile, "T1 self / T2 lab / T3", False,
                 "Profile including role, lab, training records and account codes.",
                 {"user_id": "User id; defaults to the caller."}),
        ToolSpec(2, "get_facility_catalog", get_facility_catalog, "T0", False,
                 "Facilities, instruments with hourly rates, and request templates.",
                 {"facility_id": "Optional facility filter."}),
        ToolSpec(3, "check_availability", check_availability, "T0", False,
                 "Free slots for an instrument, derived from existing bookings.",
                 {"instrument_id": "Instrument id.", "date_from": "ISO-8601 start.",
                  "date_to": "ISO-8601 end."}),
        ToolSpec(4, "get_my_bookings", get_my_bookings, "T1", False,
                 "The caller's own bookings.",
                 {"date_from": "Optional ISO-8601 start.", "date_to": "Optional ISO-8601 end."}),
        ToolSpec(5, "get_usage_records", get_usage_records, "T1/T2", False,
                 "Scheduled versus tracked hours for a user, lab or instrument.",
                 {"scope": "user | lab | instrument", "id": "Id for the chosen scope.",
                  "month": "Optional YYYY-MM filter."}),
        ToolSpec(6, "get_request_status", get_request_status, "T1/T2", False,
                 "Service request status and history.",
                 {"request_id": "Request id.", "mine": "true for the caller's requests."}),
        ToolSpec(7, "track_sample", track_sample, "T1/T2", False,
                 "Sample state timeline by barcode or id.",
                 {"barcode": "Sample barcode.", "sample_id": "Sample id."}),
        ToolSpec(8, "get_billing_summary", get_billing_summary, "T1 own / T2 / T3", False,
                 "Invoice total and lines for an account code and period.",
                 {"account_code": "Account code.", "period": "YYYY-MM."}),
        ToolSpec(9, "get_project_overview", get_project_overview, "T2/T3", False,
                 "Project members, cores used and attributed spend.",
                 {"project_id": "Project id."}),
        ToolSpec(10, "get_instrument_health", get_instrument_health, "T0 status / T3 history",
                 False, "Instrument status; maintenance history and downtime for admins.",
                 {"instrument_id": "Instrument id."}),
        ToolSpec(11, "run_readonly_sql", run_readonly_sql, "T2/T3", False,
                 "Run one validated SELECT against the four reporting views.",
                 {"sql": "A single SELECT over the allow-listed views."}),
        ToolSpec(12, "create_onboarding_request", create_onboarding_request, "T1", True,
                 "Propose onboarding a new user (pending approval).",
                 {"name": "Full name.", "email": "Email address.", "lab_id": "Lab id.",
                  "pi_ack": "PI acknowledgement, must be true.",
                  "account_code": "Optional account code."}),
        ToolSpec(13, "create_service_request", create_service_request, "T1", True,
                 "Propose a service request against a template (pending approval).",
                 {"template_id": "Template id.", "fields": "Object of template fields."}),
        ToolSpec(14, "request_booking", request_booking, "T1", True,
                 "Propose an instrument booking (pending approval).",
                 {"instrument_id": "Instrument id.", "starts_at": "ISO-8601 start.",
                  "ends_at": "ISO-8601 end.", "account_code": "Account code to charge."}),
        ToolSpec(15, "generate_document", generate_document, "T1 / T3 admin templates", True,
                 "Propose generating a document (pending approval).",
                 {"template": "usage_report | onboarding_packet | monthly_summary",
                  "params": "Template parameters."}),
        ToolSpec(16, "find_facilities", find_facilities, "T0", False,
                 "Facilities that do a technique, with distance when a location is given.",
                 {"technique": "Capability to look for, e.g. cryo-EM.",
                  "near_latitude": "Optional latitude in decimal degrees.",
                  "near_longitude": "Optional longitude in decimal degrees.",
                  "campus": "Optional campus filter."}),
        ToolSpec(17, "recommend_instrument", recommend_instrument, "T0", False,
                 "Rank instruments against a described goal, with the matching evidence.",
                 {"goal": "What the caller wants to do, in their own words.",
                  "sample_type": "Optional sample type the instrument must accept."}),
    ]
}

READ_TOOLS = [t.name for t in TOOLS.values() if not t.write]
WRITE_TOOLS = [t.name for t in TOOLS.values() if t.write]

# Wrap every handler in a trace span, and rebind the module-level names to the wrapped
# versions. Rebinding matters because the agent calls several handlers directly
# (`tools_mod.run_readonly_sql`, `tools_mod.get_user_profile`) rather than through
# `call()`, and spec 06 asks for every tool call to be traced, not just dispatched ones.
for _name, _spec in list(TOOLS.items()):
    _traced = traced_tool(_name)(_spec.handler)
    TOOLS[_name] = dataclasses.replace(_spec, handler=_traced)
    globals()[_name] = _traced


def _accepted_parameters(handler: Callable) -> tuple[set[str], bool]:
    """Parameter names a handler accepts, and whether it takes arbitrary **kwargs."""
    params = inspect.signature(handler).parameters.values()
    names = {
        p.name for p in params
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.name != "ctx"
    }
    takes_kwargs = any(p.kind is p.VAR_KEYWORD for p in params)
    return names, takes_kwargs


# What each argument is called in a sentence. Underscores alone are not enough:
# "That lookup needs date to. Say which date to you mean." is what the mechanical
# version produced, and it reads like a broken machine rather than a question.
_SPOKEN_ARGUMENTS = {
    "date_from": "a start date",
    "date_to": "an end date",
    "starts_at": "a start time",
    "ends_at": "an end time",
    "instrument_id": "an instrument",
    "project_id": "a project",
    "request_id": "a request",
    "sample_id": "a sample id",
    "account_code": "an account code",
    "template_id": "a request template",
    "user_id": "a user",
    "facility_id": "a facility",
    "subject_user_id": "a user",
    "period": "a period",
    "month": "a month",
    "barcode": "a barcode",
    "scope": "a scope",
    "fields": "the form fields",
    "template": "a template",
    "params": "parameters",
    "name": "a name",
    "email": "an email address",
    "lab_id": "a lab",
    "pi_ack": "the PI's acknowledgement",
    "mine": "whether you mean your own",
    "format": "a format",
    "sql": "a query",
    "id": "an id",
    "technique": "a technique",
    "near_latitude": "a latitude",
    "near_longitude": "a longitude",
    "campus": "a campus",
    "goal": "what you want to do",
    "sample_type": "a sample type",
}


def _spoken(names: list[str]) -> str:
    """Argument names as a person would say them. These strings reach users."""
    said = [_SPOKEN_ARGUMENTS.get(n, n.replace("_", " ")) for n in names]
    if len(said) <= 1:
        return "".join(said)
    return ", ".join(said[:-1]) + " and " + said[-1]


def accepted_arguments(name: str) -> set[str]:
    """The argument names a tool actually takes, for callers that want to repair a plan."""
    spec = TOOLS.get(name)
    if spec is None:
        return set()
    accepted, takes_kwargs = _accepted_parameters(spec.handler)
    return set(accepted) if not takes_kwargs else set()


def required_arguments(name: str) -> set[str]:
    """Arguments with no default — the ones a caller must supply."""
    spec = TOOLS.get(name)
    if spec is None:
        return set()
    params = inspect.signature(spec.handler).parameters.values()
    return {
        p.name for p in params
        if p.name != "ctx"
        and p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }


def call(ctx: Ctx, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single dispatch point used by the MCP server and the agent.

    Arguments are checked against the handler signature before dispatch. Splatting an
    unvalidated dict raised a bare TypeError whose text — "get_my_bookings() got an
    unexpected keyword argument 'subject_user_id'" — went straight to the user, leaking
    an internal signature and replacing an honest refusal with a stack-trace fragment.
    A caller sending a key a tool does not take is making a bad request, and should be
    told so as a typed error like every other bad request.
    """
    spec = TOOLS.get(name)
    if spec is None:
        raise invalid_params(f"Unknown tool {name!r}.", f"Known tools: {', '.join(TOOLS)}.")

    arguments = arguments or {}
    accepted, takes_kwargs = _accepted_parameters(spec.handler)
    if not takes_kwargs:
        unexpected = sorted(set(arguments) - accepted)
        if unexpected:
            raise invalid_params(
                f"That lookup does not take {_spoken(unexpected)}.",
                f"It takes {_spoken(sorted(accepted)) or 'no arguments'}.",
            )

    # The mirror image, and the same bug: an argument a tool requires and did not get
    # reached the handler and raised a bare TypeError — "get_project_overview() missing 1
    # required positional argument: 'project_id'" — which is a stack-trace fragment where
    # a refusal belongs. Checking one direction and not the other only moved the leak.
    missing = sorted(required_arguments(name) - set(arguments))
    if missing:
        raise invalid_params(
            f"That lookup needs {_spoken(missing)}.",
            "Add that and I will run it.",
        )
    return spec.handler(ctx, **arguments)
