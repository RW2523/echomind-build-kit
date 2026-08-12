"""The action branch: build an exact payload, propose it, and stop.

Nothing here mutates Infinity X. The write tools (12–15) produce a pending action; the
graph then interrupts and waits for a human decision delivered through
POST /actions/{id}/approve or /decline.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from server.agent.llm import chat_json
from server.agent.prompts import register
from server.agent.responses import AgentResponse
from server.auth import Ctx
from server.db import session_scope
from server.mcp import tools as tools_mod
from server.mcp.errors import ToolError

log = logging.getLogger("echomind.action")

WRITE_TOOLS = """create_onboarding_request(name, email, lab_id, pi_ack, account_code?)
    Propose onboarding a new user. pi_ack must be true.
create_service_request(template_id, fields)
    Propose a service request. fields must satisfy the template's required fields.
request_booking(instrument_id, starts_at, ends_at, account_code)
    Propose an instrument booking. ISO-8601 UTC timestamps, max 12 hours.
generate_document(template, params)
    template is one of usage_report, onboarding_packet, monthly_summary."""

SYSTEM = """You turn a user's request into exactly one write-tool call for the Infinity X
platform. You never execute anything: the call you describe becomes a pending action that
a human must approve.

Reply only as JSON: {{"tool": "<name>", "arguments": {{...}}}}
If the request does not match any tool, reply {{"tool": null, "why": "<12 words"}}.
If it matches a tool but you are missing something only the user can supply, reply
{{"tool": null, "missing": ["field"], "ask": "<one short question>"}} — never invent the
value and never assume a consent or acknowledgement the user has not actually given.

Tools:
{tools}

Context you must use rather than invent:
- Today is {today} (UTC). Resolve relative dates against it.
- The caller is {user_id} ({role}), labs: {labs}.
- The caller's account codes: {codes}
- Instruments (id — name — status):
{instruments}
- Request templates (id — name — required fields):
{templates}

Rules:
- Resolve pronouns from the conversation: "book it" means the instrument just discussed.
- Use the instrument *id*, never its display name.
- Never guess an account code: use one of the caller's. If they have none, use null.
- Timestamps are ISO-8601 with a Z suffix, e.g. 2026-04-02T09:00:00Z.
- A booking sits inside opening hours ({hours}), ends on the day it starts, and runs no
  longer than 12 hours, so a whole calendar day is never a valid one. Ask for a start
  time if the conversation settled only a date.
- "Submit", "file" or "raise" a request means create_service_request. generate_document
  produces the three named report templates and nothing else — it is never how a filled
  form gets submitted.
- Take the date and time from the conversation. If an earlier turn discussed a specific
  date and window, "book it" means exactly that date and window — do not substitute
  today's date. Fall back to the next occurrence after today only when no date appears
  anywhere in the conversation.
- pi_ack is true ONLY if the user has actually said the PI acknowledges it. Being a PI
  is not the same as having said so. If they have not, ask.
- Enum fields must use one of the listed options exactly as written ("150bp", not "150").
- Take field values from the user's own words or from their documents. Never invent one."""


def _template_line(t) -> str:
    """One template with its required fields and, for enums, the exact allowed values."""
    parts = []
    for field in t["fields"]:
        if not field.get("required"):
            continue
        if field.get("type") == "enum":
            parts.append(f"{field['name']} (one of: {'|'.join(field.get('options', []))})")
        else:
            parts.append(f"{field['name']} ({field.get('type', 'string')})")
    return f"    {t['id']} — {t['name']} — requires: " + ", ".join(parts)


def _catalog(ctx: Ctx) -> dict[str, str]:
    with session_scope() as s:
        instruments = s.execute(
            text("SELECT id, name, status FROM infinity.instruments ORDER BY name")
        ).mappings().all()
        templates = s.execute(
            text("SELECT id, name, fields FROM infinity.request_templates ORDER BY name")
        ).mappings().all()
        codes = s.execute(
            text("SELECT account_codes FROM infinity.users WHERE id = :id"),
            {"id": ctx.user_id},
        ).scalar_one_or_none()

    return {
        "instruments": "\n".join(
            f"    {i['id']} — {i['name']} — {i['status']}" for i in instruments
        ),
        "templates": "\n".join(_template_line(t) for t in templates),
        "codes": ", ".join(codes or []) or "none",
    }


def _document_context(message: str, ctx: Ctx) -> str:
    """Documents the caller can see that bear on this request.

    Scene 4 of the demo submits a filled form the user uploaded: the planner has to read
    the form to fill the template's fields. Retrieval is the permission-filtered path, so
    this cannot surface a document the caller is not entitled to.
    """
    try:
        from server.rag.retrieval import retrieve

        chunks = retrieve(message, ctx, k=3)
    except Exception as exc:
        log.warning("could not load document context: %s", exc)
        return ""
    if not chunks:
        return ""
    body = "\n\n".join(f"[{c.breadcrumb}]\n{c.text}" for c in chunks)
    return (
        "\n\nDOCUMENTS THE CALLER CAN SEE (use these to fill in field values the user "
        f"has already written down; do not invent values):\n{body}"
    )


_WORD_QUANTITIES = {
    "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0, "half an": 0.5,
    "half": 0.5,
}
_DURATION_RE = re.compile(
    r"\b(?P<qty>\d+(?:\.\d+)?|half\s+an|half|an|a|one|two|three|four|five|six|seven|"
    r"eight|nine|ten)[\s-]*(?P<unit>hours?|hrs?|h|minutes?|mins?)\b",
    re.IGNORECASE,
)


def stated_duration(message: str) -> timedelta | None:
    """The booking length the user said out loud, if they said one.

    "for 2 hours", "90 minutes", "a 2-hour slot", "half an hour".
    """
    match = _DURATION_RE.search(message)
    if match is None:
        return None
    raw = re.sub(r"\s+", " ", match.group("qty").strip().lower())
    try:
        qty = float(raw) if raw[0].isdigit() else _WORD_QUANTITIES[raw]
    except (KeyError, ValueError):
        return None
    minutes = qty * (60 if match.group("unit").lower().startswith(("h",)) else 1)
    if not 0 < minutes <= 12 * 60:
        # Out of range is the tool's refusal to make, with its own wording. Leaving the
        # planner's window alone keeps that error honest instead of manufacturing one.
        return None
    return timedelta(minutes=minutes)


def apply_stated_duration(plan: dict[str, Any], message: str) -> dict[str, Any]:
    """Make the proposed window as long as the user actually asked for.

    The planner resolves "book it" by copying the window out of the conversation, which is
    right, and then copies it *whole* — so "then book it for 2 hours" after a full-day
    availability check proposed 00:00 to 00:00 the next day, and the user who asked for
    two hours was told a booking may not exceed twelve. Deterministic 3/3.

    The end moves, never the start: the start is the one thing the conversation genuinely
    established. A window that already matches is left untouched, and anything unparseable
    is left alone rather than guessed at.
    """
    if plan.get("tool") != "request_booking":
        return plan
    wanted = stated_duration(message)
    if wanted is None:
        return plan

    arguments = plan.get("arguments")
    if not isinstance(arguments, dict):
        return plan
    starts_at, ends_at = arguments.get("starts_at"), arguments.get("ends_at")
    if not isinstance(starts_at, str) or not isinstance(ends_at, str):
        return plan
    try:
        start = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
    except ValueError:
        return plan

    if end - start == wanted:
        return plan
    corrected = (start + wanted).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info(
        "user said %s; correcting proposed window %s..%s to end %s",
        wanted, starts_at, ends_at, corrected,
    )
    arguments["ends_at"] = corrected
    return plan


# "Confocal C2", "ins-confocal-c2", or just "C2" — people name instruments by their model
# number and the planner has to follow. Letters then digits, which is what every model
# number in the catalogue looks like and what no ordinary word does.
_MODEL_TOKEN_RE = re.compile(r"\b[A-Za-z]{1,3}\d{1,3}\b")


def _instrument_rows() -> list[tuple[str, str]]:
    with session_scope() as s:
        return [
            (r["id"], r["name"])
            for r in s.execute(
                text("SELECT id, name FROM infinity.instruments")
            ).mappings().all()
        ]


def instruments_mentioned(text_: str, rows: list[tuple[str, str]]) -> list[str]:
    """Instrument ids named in this text, in the order they are mentioned."""
    hits: dict[str, int] = {}
    for iid, name in rows:
        for needle in (name, iid, *_MODEL_TOKEN_RE.findall(name)):
            match = re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", text_, re.I)
            if match:
                hits[iid] = min(hits.get(iid, match.start()), match.start())
                break
    return [iid for iid, _ in sorted(hits.items(), key=lambda kv: kv[1])]


# Words that identify a kind of instrument rather than a specific one. Derived from the
# catalogue by dropping the model number, so it stays right as instruments are added.
_FAMILY_STOPWORDS = frozenset({"the", "a", "an"})


def instrument_family_mentioned(
    text_: str, rows: list[tuple[str, str]]
) -> list[str]:
    """Instruments whose *kind* this text names — "the confocal" -> both confocals.

    Names in the catalogue are a kind followed by a model: "Confocal C2", "Light Sheet
    LS7", "Cryo-EM Titan". People say the kind. Returns every instrument of every kind
    named, so the caller can decide whether that is specific enough to act on.
    """
    found: dict[str, int] = {}
    for iid, name in rows:
        # The kind is the name without its model: "Confocal C2" -> Confocal, "Cryo-EM
        # Titan" -> Cryo-EM, "Spinning Disk SD1" -> Spinning Disk. Dropping the last
        # token rather than only digit-bearing ones, because plenty of models are words
        # (Titan, Exploris, PromethION) and "the cryo-EM" has to work too.
        words = [w for w in name.split() if w.lower() not in _FAMILY_STOPWORDS]
        kind = " ".join(words[:-1]).strip() if len(words) > 1 else ""
        if len(kind) < 3:
            continue
        # "Cryo-EM" is also said "cryo em"; "Light Sheet" as "lightsheet".
        pattern = r"[\s\-]*".join(re.escape(part) for part in re.split(r"[\s\-]+", kind))
        match = re.search(rf"(?<![\w-]){pattern}(?![\w-])", text_, re.I)
        if match:
            found[iid] = match.start()
    return [iid for iid, _ in sorted(found.items(), key=lambda kv: kv[1])]


def carry_forward_instrument(
    plan: dict[str, Any], message: str, history: str
) -> dict[str, Any]:
    """Book the instrument the conversation is about, not a different one.

    "then book it for 2 hours" resolved to Confocal C2 correctly; "make it 3 hours
    instead" proposed Cryo-EM Titan and "actually just half an hour" proposed Spinning
    Disk, from the same conversation. Once a follow-up names no instrument the planner
    has nothing to anchor on and picks one — and "it" in "make it 3 hours" points at the
    duration, so the instrument has to come from the conversation or from nowhere.

    The approval card would have caught it, which is exactly why it is not a safety bug
    and exactly why it still has to be fixed: nobody reads a card that is usually right.
    """
    if plan.get("tool") != "request_booking":
        return plan
    arguments = plan.get("arguments")
    if not isinstance(arguments, dict):
        return plan

    rows = _instrument_rows()
    earlier = instruments_mentioned(history, rows)
    named_now = instruments_mentioned(message, rows)

    if len(named_now) == 1:
        intended = named_now[0]
    elif named_now:
        return plan  # genuinely ambiguous — let the tool or the user settle it
    else:
        # Nobody says "Confocal C2" twice. They say "the confocal", and the exact matcher
        # sees nothing — so "OK, back to the confocal. Book it..." fell through to the
        # last instrument mentioned anywhere in the conversation and proposed the Light
        # Sheet, which was under maintenance. The user had named the instrument; we were
        # not listening.
        family = instrument_family_mentioned(message, rows)
        if len(family) == 1:
            intended = family[0]
        elif family:
            # Several confocals. The one the conversation was already about is the one
            # they meant; if the conversation has not settled on one, ask, because "the
            # confocal" genuinely does not say which and the two have different rates.
            # "BOOK THE CONFOCAL NOW!!!" proposed C3 with nothing behind the choice.
            from_history = [iid for iid in earlier if iid in family]
            if len(set(from_history)) != 1:
                names = [name for iid, name in rows if iid in family]
                log.info("'%s' names %d instruments; asking", message[:40], len(family))
                return {
                    "tool": None,
                    "missing": ["instrument_id"],
                    "ask": f"Which one — {' or '.join(sorted(names))}?",
                }
            intended = from_history[-1]
        elif earlier:
            intended = earlier[-1]
        else:
            return plan

    if arguments.get("instrument_id") != intended:
        log.info(
            "instrument: planner said %s, conversation says %s",
            arguments.get("instrument_id"), intended,
        )
        arguments["instrument_id"] = intended
    return plan


def require_supplied_identity(plan: dict[str, Any], source: str) -> dict[str, Any]:
    """An onboarding proposal must name a person the user actually named.

    Asked to "onboard a new user for Lab A", the planner proposed onboarding
    "New User <newuser@example.com>" — a complete, plausible, entirely fictional pending
    action. The prompt already says never invent a value; this checks, because a
    fabricated name and address is the one mistake an approval card is least likely to
    catch: it looks exactly like a real proposal, and the approver has no way to know the
    person does not exist.

    `source` is everything the user has actually put in front of us — this message, the
    conversation, and any document they uploaded — so a name read off a form still passes.
    """
    if plan.get("tool") != "create_onboarding_request":
        return plan
    arguments = plan.get("arguments")
    if not isinstance(arguments, dict):
        return plan

    haystack = source.lower()
    invented = [
        name for name in ("name", "email")
        if not str(arguments.get(name) or "").strip()
        or str(arguments[name]).strip().lower() not in haystack
    ]
    if not invented:
        return plan

    log.warning("onboarding proposal invented %s; asking instead", invented)
    return {
        "tool": None,
        "missing": invented,
        "ask": "Who am I onboarding? I need their full name and their email address.",
    }


def plan(message: str, ctx: Ctx, history: str = "") -> dict[str, Any]:
    catalog = _catalog(ctx)
    system = SYSTEM.format(
        tools=WRITE_TOOLS,
        hours=f"{tools_mod.OPEN_HOUR:02d}:00-{tools_mod.CLOSE_HOUR:02d}:00 UTC",
        today=datetime.now(UTC).date().isoformat(),
        user_id=ctx.user_id,
        role=ctx.role,
        labs=", ".join(ctx.lab_ids) or "none",
        **catalog,
    )
    user = message
    if history:
        user = f"{history}\n\nCURRENT REQUEST: {message}"
    documents = _document_context(message, ctx)
    user += documents

    chosen = chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        default={"tool": None, "why": "planner unavailable"},
        max_tokens=400,
    )
    # What the user actually said wins over what the planner inherited or invented.
    chosen = apply_stated_duration(chosen, message)
    chosen = carry_forward_instrument(chosen, message, history)
    return require_supplied_identity(chosen, f"{message}\n{history}\n{documents}")


def propose(
    message: str, ctx: Ctx, thread_id: str | None = None, history: str = ""
) -> AgentResponse:
    """Create the pending action and return the approval request."""
    chosen = plan(message, ctx, history)
    name = chosen.get("tool")

    if name not in tools_mod.WRITE_TOOLS:
        # Missing information the user alone can supply: ask, rather than inventing it
        # or firing a call that the tool will only reject.
        question = str(chosen.get("ask") or "").strip()
        if question:
            return AgentResponse(
                response_type="redirect",
                route="action",
                text=question,
                meta={"plan": chosen, "awaiting": chosen.get("missing")},
            )
        return AgentResponse(
            response_type="redirect",
            route="action",
            text=(
                "I can propose bookings, service requests, onboarding requests and "
                "facility documents. I could not map that request onto any of them — "
                "could you say which one you meant?"
            ),
            meta={"plan": chosen},
        )

    arguments = chosen.get("arguments") or {}
    try:
        pending = tools_mod.call(ctx, name, arguments)
    except ToolError as exc:
        return AgentResponse(
            response_type="redirect",
            route="action",
            text=(
                f"I could not prepare that request. {exc.message} {exc.hint}".strip()
            ),
            meta={"error": exc.to_dict()["error"], "plan": chosen},
        )

    if thread_id:
        with session_scope() as s:
            s.execute(
                text("UPDATE echomind.actions SET thread_id = :t WHERE id = :id"),
                {"t": thread_id, "id": pending["action_id"]},
            )

    return AgentResponse(
        response_type="approval_request",
        route="action",
        text=(
            f"I've prepared this, but I haven't done it. {pending['payload_preview']}.\n\n"
            "Approve it and I'll execute it; decline and nothing happens. "
            "Either way it goes in the audit log."
        ),
        pending_action=pending,
        meta={"plan": chosen},
    )


def confirmation_text(action: dict[str, Any]) -> str:
    """Post-decision message. Every value comes from the action's stored result row."""
    status = action.get("status")
    result = action.get("result") or {}

    if status == "declined":
        return "Declined — nothing was changed. The decision is recorded in the audit log."

    if status == "failed":
        return (
            "The approval went through but the change did not: "
            f"{result.get('error', 'the platform rejected it')}. "
            "Nothing was left half-done, and the failure is in the audit log."
        )

    created = result.get("created")
    if created == "booking":
        # Report the status the row actually has — the facility still confirms the slot.
        return (
            f"Done. Booking {result['booking_id']} is now '{result['status']}' on "
            f"{result['instrument_id']} from {result['starts_at']} to {result['ends_at']}."
        )
    if created == "service_request":
        return (
            f"Submitted. Request {result['request_id']} is now "
            f"'{result['status']}' against template {result['template_id']}."
        )
    if created == "user":
        return (
            f"Onboarded. {result['email']} now exists as {result['user_id']}. "
            "They will need instrument training before they can book."
        )
    if created == "document":
        return (
            f"Generated {result['title']} — saved to {result['path']} "
            f"({result['bytes']} bytes)."
        )
    return f"Done. The action completed with status '{status}'."

# Versioned by content hash — see server/agent/prompts.py.
VERSION_SYSTEM = register("action.system", SYSTEM)
