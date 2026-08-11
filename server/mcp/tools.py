"""The 15 tools.

Every handler takes a verified `Ctx` and enforces its tier BEFORE running any query
(spec 02). These functions are the single implementation: the MCP server exposes them
over the wire and the LangGraph agent calls them in-process, so there is exactly one
enforcement path and one thing to test.

Tier matrix — spec 05:
    T0  any authenticated user            2, 3, 10(status)
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

DOCUMENT_TEMPLATES = ("usage_report", "onboarding_packet", "monthly_summary")
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
        raise invalid_params("scope must be one of: user, lab, instrument.")
    month = _check_month(month)

    with session_scope() as s:
        if scope == "user":
            target = id or ctx.user_id
            _resolve_target_user(ctx, s, target)
            where, params = "user_id = :target", {"target": target}
        elif scope == "lab":
            if not id:
                raise invalid_params("scope='lab' requires the lab id in `id`.")
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
        raise invalid_params("Pass request_id, or mine=true for your own requests.")

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
        raise invalid_params("Pass either barcode or sample_id.")

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
        raise invalid_params("ends_at must be after starts_at.")
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


def _spoken(names: list[str]) -> str:
    """Argument names as a sentence. These strings reach users, so `date_from` does not."""
    return ", ".join(n.replace("_", " ") for n in names)


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
            f"Say which {_spoken(missing)} you mean.",
        )
    return spec.handler(ctx, **arguments)
