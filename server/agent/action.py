"""The action branch: build an exact payload, propose it, and stop.

Nothing here mutates Infinity X. The write tools (12–15) produce a pending action; the
graph then interrupts and waits for a human decision delivered through
POST /actions/{id}/approve or /decline.
"""

from __future__ import annotations

import calendar
import logging
import re
import textwrap
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text

from server.agent import progress
from server.agent.llm import chat_json
from server.agent.prompts import register
from server.agent.responses import AgentResponse
from server.auth import Ctx
from server.db import session_scope
from server.mcp import tools as tools_mod
from server.mcp.errors import ToolError

log = logging.getLogger("echomind.action")

# Constraints the planner needs that a ToolSpec has no field for. Guidance only — which
# tools exist, and what they take, is read from the registry below.
_PLANNER_NOTES = {
    "create_onboarding_request": "Propose onboarding a new user. pi_ack must be true. "
                                 "account_code is optional.",
    "create_service_request": "Propose a service request. fields must satisfy the "
                              "template's required fields.",
    "request_booking": "Propose an instrument booking. ISO-8601 UTC timestamps, "
                       "max 12 hours.",
    "cancel_booking": "Propose cancelling a booking the caller may act on. reason is "
                      "optional. The charge the rules give is worked out and shown on "
                      "the card; never state or guess a charge yourself.",
    "reschedule_booking": "Propose moving a booking to a new slot. ISO-8601 UTC "
                          "timestamps, max 12 hours.",
    "generate_document": "template is one of usage_report, onboarding_packet, "
                         "monthly_summary, invoice_statement (params: account_code, "
                         "period), facility_directory (params: technique?, campus?), "
                         "capability_report (params: goal), booking_confirmation "
                         "(params: booking_id), usage_summary (params: month?).",
}


def _write_tool_menu() -> str:
    """The menu the planner is shown, built from the registry rather than retyped.

    This was a hand-written string, and cancel_booking and reschedule_booking were
    registered, tiered, tested and callable while being absent from it — so the planner
    could not propose them and "cancel booking bk-0133" produced a new booking instead.
    Registering a tool is not the same as offering it, and nothing connected the two.

    Now a write tool cannot be unreachable: it appears the moment it is registered, with
    its real parameter list. _PLANNER_NOTES only adds guidance; a tool with no note still
    appears, described by its own ToolSpec.
    """
    lines: list[str] = []
    for name in tools_mod.WRITE_TOOLS:
        spec = tools_mod.TOOLS[name]
        lines.append(f"{name}({', '.join(spec.params)})")
        note = _PLANNER_NOTES.get(name) or spec.description
        lines.append(textwrap.indent(textwrap.fill(note, width=84), "    "))
    return "\n".join(lines)


WRITE_TOOLS = _write_tool_menu()

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
  produces the named report templates and nothing else — it is never how a filled form
  gets submitted.
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
    # Only what the caller wrote down themselves. Retrieval also returns public policy
    # text — the same permission filter admits it, correctly — and one heading told the
    # planner all of it was "values the user has already written down". With no form
    # uploaded, "Bulk RNA-seq: 15 working days" from the turnaround policy came back as
    # sample_count=15 on an approval card: a turnaround time, presented as a sample count,
    # for a human to approve. A policy is not something the user filled in, so it does not
    # get to fill in a form on their behalf. Public and lab documents are still shown, so
    # the planner can see what a template expects — but under a heading that says what
    # they are, and with the instruction that field values may only come from the caller's
    # own documents.
    own = [c for c in chunks if c.visibility == "private"]
    shared = [c for c in chunks if c.visibility != "private"]
    parts = []
    if own:
        body = "\n\n".join(f"[{c.breadcrumb}]\n{c.text}" for c in own)
        parts.append(
            "\n\nTHE CALLER'S OWN DOCUMENTS (the only source for field values — copy them "
            f"exactly; do not invent or infer a value):\n{body}"
        )
    if shared:
        body = "\n\n".join(f"[{c.breadcrumb}]\n{c.text}" for c in shared)
        parts.append(
            "\n\nSHARED POLICY AND REFERENCE TEXT (background only; never a source of "
            f"field values — if a field is not in the caller's own documents, ask):\n{body}"
        )
    return "".join(parts)


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


_REL_DAY = (
    (re.compile(r"\bday after tomorrow\b", re.I), 2),
    (re.compile(r"\btomorrow\b", re.I), 1),
    (re.compile(r"\btoday\b", re.I), 0),
)
_REL_IN_DAYS = re.compile(r"\bin\s+(\d{1,3})\s+days?\b", re.I)
_REL_MONTH = (
    (re.compile(r"\bnext month\b", re.I), 1),
    (re.compile(r"\bthis month\b", re.I), 0),
)
_REL_IN_MONTHS = re.compile(r"\bin\s+(\d{1,2})\s+months?\b", re.I)


def _add_months(year: int, month: int, k: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + k
    return index // 12, index % 12 + 1


def relative_date_target(message: str, today: date):
    """What date, if any, a relative phrase in the message names.

    Returns ("day", date) for a day-precise phrase, ("month", (year, month)) for a
    month-granularity one, or None. "next week" is deliberately not handled: it names a
    week, not a day, and guessing the day would be worse than leaving it.
    """
    for rx, delta in _REL_DAY:
        if rx.search(message):
            return "day", today + timedelta(days=delta)
    if (m := _REL_IN_DAYS.search(message)):
        return "day", today + timedelta(days=int(m.group(1)))
    for rx, k in _REL_MONTH:
        if rx.search(message):
            return "month", _add_months(today.year, today.month, k)
    if (m := _REL_IN_MONTHS.search(message)):
        return "month", _add_months(today.year, today.month, int(m.group(1)))
    return None


def apply_relative_date(
    plan: dict[str, Any], message: str, today: date | None = None
) -> dict[str, Any]:
    """Make the proposed date honour a relative phrase the user actually said.

    "Book it next month" after an availability check for 2027-04-15 proposed 2026-08-16 —
    the current month, 3/3 — dropping the instruction entirely. The date moves; the
    time-of-day and the duration are kept. A month-granularity phrase keeps the planner's
    day-of-month (clamped) so "next month" lands on the same day one month on, and does
    nothing if the proposal is already in the requested month.
    """
    if plan.get("tool") != "request_booking":
        return plan
    arguments = plan.get("arguments")
    if not isinstance(arguments, dict):
        return plan
    target = relative_date_target(message, today or datetime.now(UTC).date())
    if target is None:
        return plan
    starts_at, ends_at = arguments.get("starts_at"), arguments.get("ends_at")
    if not (isinstance(starts_at, str) and isinstance(ends_at, str)):
        return plan
    try:
        start = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
    except ValueError:
        return plan

    kind, value = target
    if kind == "day":
        new_date = value
    else:
        year, month = value
        if (start.year, start.month) == (year, month):
            return plan  # already in the requested month
        day = min(start.day, calendar.monthrange(year, month)[1])
        new_date = date(year, month, day)

    new_start = start.replace(year=new_date.year, month=new_date.month, day=new_date.day)
    if new_start == start:
        return plan
    new_end = new_start + (end - start)
    arguments["starts_at"] = new_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    arguments["ends_at"] = new_end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("relative date %r -> %s", message[:40], arguments["starts_at"])
    return plan


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

# Generic references that name no specific instrument at all — a category, or none.
_GENERIC_INSTRUMENT_RE = re.compile(
    r"\b(scope|microscope|instrument|machine|sequencer|imager|analy[sz]er|"
    r"a\s+tool|some\s+equipment)\b",
    re.IGNORECASE,
)


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
    choice, family = _referenced_instrument(message, history, rows)

    # A generic word — "book a scope", "book the microscope", "book an instrument" — names
    # no specific machine, and the planner just picked one (Confocal C2) with nothing behind
    # it. Ask which, the same as an ambiguous kind, rather than proposing a guess.
    if choice is None and not family and _GENERIC_INSTRUMENT_RE.search(message):
        log.info("generic instrument term with no specific one; asking")
        return {
            "tool": None,
            "missing": ["instrument_id"],
            "ask": "Which instrument would you like to book?",
        }

    if choice is None and family:
        # A kind was referenced but nothing settles which one — two confocals with
        # different rates. If the planner already picked inside that kind, that is at
        # least a confocal, but the pick is arbitrary; if it picked OUTSIDE the kind (the
        # "book it next month" thread proposed Spinning Disk after a confocal question),
        # it is simply wrong. Either way, ask rather than guess.
        names = sorted(name for iid, name in rows if iid in family)
        log.info("instrument reference is ambiguous across %s; asking", names)
        return {
            "tool": None,
            "missing": ["instrument_id"],
            "ask": f"Which one — {' or '.join(names)}?",
        }
    if choice is None:
        return plan  # no instrument referenced anywhere — leave the planner's pick

    if arguments.get("instrument_id") != choice:
        log.info(
            "instrument: planner said %s, conversation says %s",
            arguments.get("instrument_id"), choice,
        )
        arguments["instrument_id"] = choice
    return plan


def _referenced_instrument(
    message: str, history: str, rows: list[tuple[str, str]]
) -> tuple[str | None, list[str]]:
    """Which instrument the conversation is pointing at, by the strongest signal available.

    Returns (concrete_id, []) when one instrument is unambiguously meant, or
    (None, family) when only a kind is referenced and more than one instrument fits, so
    the caller can ask. Priority, strongest first: an instrument named outright in this
    message; a kind named in this message (disambiguated by what was concretely discussed
    earlier); an instrument named outright earlier; a kind named earlier. "the confocal"
    said now, after "Confocal C2" earlier, means C2 — the intersection settles it.
    """
    msg_concrete = _unique(instruments_mentioned(message, rows))
    if len(msg_concrete) == 1:
        return msg_concrete[0], []
    if msg_concrete:
        return None, msg_concrete  # two named at once — ambiguous

    hist_concrete = _unique(instruments_mentioned(history, rows))
    msg_family = _unique(instrument_family_mentioned(message, rows))
    if msg_family:
        settled = [i for i in hist_concrete if i in msg_family]
        if len(_unique(settled)) == 1:
            return settled[0], []
        if len(msg_family) == 1:
            return msg_family[0], []
        return None, msg_family

    if hist_concrete:
        # By where it was last SAID, not where it was first said. _unique keeps
        # first-appearance order, so [-1] meant "the last instrument to be introduced",
        # which is a different thing the moment one turn names several: a recommendation
        # answering "Confocal C2, Confocal C3 and Spinning Disk SD1 are suitable. Light
        # Sheet LS7 is excluded due to mismatched techniques" put Light Sheet last in that
        # ordering and kept it there. The next two turns were about Confocal C2, and "book
        # it from 9am" proposed the Light Sheet — the one instrument in the thread that had
        # been named in order to rule it out.
        return _last_mentioned(history, rows) or hist_concrete[-1], []

    hist_family = _unique(instrument_family_mentioned(history, rows))
    if len(hist_family) == 1:
        return hist_family[0], []
    if hist_family:
        return None, hist_family
    return None, []


def _unique(items: list[str]) -> list[str]:
    """Distinct, order preserved."""
    return list(dict.fromkeys(items))


def _last_mentioned(text_: str, rows: list[tuple[str, str]]) -> str | None:
    """The instrument named latest in this text, by position of its LAST occurrence.

    instruments_mentioned answers "which were named, in the order they first appear",
    which is the right question for reading a sentence and the wrong one for deciding
    what a conversation is currently about.
    """
    latest: str | None = None
    latest_at = -1
    for iid, name in rows:
        for needle in (name, iid, *_MODEL_TOKEN_RE.findall(name)):
            for match in re.finditer(
                rf"(?<![\w-]){re.escape(needle)}(?![\w-])", text_, re.IGNORECASE
            ):
                if match.start() > latest_at:
                    latest, latest_at = iid, match.start()
    return latest


# "as a pdf", "in word", "give me a PDF of it". The tool has always accepted a format and
# the planner reliably dropped it: a user asked to "convert into a pdf" and approved a card
# that said "as MD", because the card was accurate about what it was about to do.
_FORMAT_WORDS = (
    ("pdf", re.compile(r"\bpdfs?\b", re.I)),
    ("docx", re.compile(r"\b(docx|word document|word doc|word file|as word|in word)\b", re.I)),
    ("md", re.compile(r"\b(markdown|\.md)\b", re.I)),
)

# What the conversation was just about, and the document that reports it. Ordered: the
# first match in the most recent text wins, so a thread that moved from usage to invoices
# converts the invoice.
_DOCUMENT_SUBJECTS = (
    ("invoice_statement", re.compile(r"\binvoice|billing|charged|statement\b", re.I)),
    ("usage_summary", re.compile(r"\busage|scheduled hours|tracked hours\b", re.I)),
    ("booking_confirmation", re.compile(r"\bbooking|booked|reservation\b", re.I)),
    ("capability_report", re.compile(r"\brecommend|which instrument|suited|capability\b", re.I)),
    ("facility_directory", re.compile(r"\bfacilit|core|campus|nearest\b", re.I)),
)
_CONVERT_RE = re.compile(
    r"\b(convert|export|download|save|turn (?:it|this|that) into|as a|give me)\b.{0,40}"
    r"\b(pdf|docx|word|markdown|document|file|copy)\b|\b(pdf|docx) (?:of|for) (?:it|this|that)\b",
    re.IGNORECASE | re.DOTALL,
)


def stated_format(message: str) -> str | None:
    """The file format the user actually named, if they named one."""
    for fmt, pattern in _FORMAT_WORDS:
        if pattern.search(message):
            return fmt
    return None


def apply_stated_format(plan: dict[str, Any], message: str) -> dict[str, Any]:
    """Give the document the format the user asked for.

    The word was in their message and the tool has always taken it; only the planner had
    to remember to pass it along, and it did not. Deterministic, so "pdf" means pdf.
    """
    if plan.get("tool") != "generate_document":
        return plan
    arguments = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}
    fmt = stated_format(message)
    if not fmt:
        return plan
    return {**plan, "arguments": {**arguments, "format": fmt}}


def carry_forward_document_subject(
    plan: dict[str, Any], message: str, history: str
) -> dict[str, Any]:
    """"Convert it to a pdf" means the thing we were just talking about.

    Shown an invoice and asked to convert it, the planner answered "What document would
    you like to convert into a PDF?" — the user had to name again what was already on
    screen. It does not fail by picking the wrong template; it fails by having no template
    at all and asking, which is why the guard has to run when the plan is an ASK as much as
    when it is a half-filled call. The knowledge branch resolves pronouns across turns;
    this is the same courtesy for documents.

    Only fires on an explicit convert/export phrasing, and only when the conversation
    actually names a subject — so "generate a document" with nothing behind it still asks.
    """
    # Either the user said "convert it", or they are answering a question we asked about
    # a document — in which case the subject is whatever we were asking about, and
    # re-planning from two words loses it. Asked "which account code should the invoice
    # be for?" and told "ACC-A1", the planner proposed a booking confirmation for a
    # booking id it made up, because "ACC-A1" on its own says nothing about invoices.
    if not (_CONVERT_RE.search(message) or _answering_a_document_question(history)):
        return plan
    tool = plan.get("tool")
    if tool not in (None, "generate_document"):
        return plan
    arguments = plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}
    # A template the planner chose is normally left alone — but not when the user is
    # answering a question we asked about a different document. Told "ACC-A1" after
    # "which account code should the invoice be for?", the planner proposed a booking
    # confirmation for a booking id it invented; deferring to that would be deferring to
    # a guess about which document the conversation is even about.
    answering = _answering_a_document_question(history)
    if arguments.get("template") and not answering:
        return plan

    haystack = f"{message}\n{history or ''}"
    for template, pattern in _DOCUMENT_SUBJECTS:
        if not (pattern.search(message) or pattern.search(history or "")):
            continue
        # Start clean when overriding: the planner's params belong to the template it
        # picked, and carrying a booking_id onto an invoice is how a wrong id survives
        # the correction that was supposed to remove it.
        params = {} if (answering and arguments.get("template") != template) else dict(
            arguments.get("params") or {}
        )
        params.update(_document_params_from(template, haystack))
        if _MISSING_FOR.get(template, ()) and not all(
            params.get(k) for k in _MISSING_FOR[template]
        ):
            # The conversation named the subject but not enough of it to render. Let the
            # planner's own question stand rather than proposing a card that would fail.
            return plan
        log.info("resolving %r to %s from the conversation", message[:40], template)
        return {
            "tool": "generate_document",
            "arguments": {
                "template": template,
                "params": params,
                "format": arguments.get("format") or stated_format(message)
                or tools_mod.DEFAULT_DOCUMENT_FORMAT,
            },
        }
    return plan


# Documents whose whole meaning is the window they cover, and the parameter that carries
# it. Rendering one for a period nobody named produces a plausible, wrong, signed-looking
# artefact — the exact failure this system exists to prevent — so it asks instead.
#
# The question carries no worked example on purpose. It used to read "For example March
# 2026, or 2026-03", which went into the history verbatim — and the next turn found those
# dates while looking for the period the *user* had named. Whatever they replied, the
# guard grounded the invoice in its own suggestion and rendered March. A guard whose own
# question satisfies it is not a guard.
_PERIOD_REQUIRED = {
    "invoice_statement": (
        "period",
        "Which period should the invoice cover? Tell me the month and the year.",
    ),
    "usage_summary": (
        "month",
        "Which month should the usage summary cover? Tell me the month and the year.",
    ),
    "monthly_summary": (
        "month",
        "Which month should the summary cover? Tell me the month and the year.",
    ),
}

# Month names and their standard abbreviations, and nothing else. This was written as
# `(jan|feb|mar|…)[a-z]*`, which matches every English word that merely begins with one:
# "maybe" was a May invoice, "Mark" a March invoice, "declined" a December invoice. The
# guard built to stop invented periods was the thing inventing them.
_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sept(?:ember)?|sep|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_MONTH_NUMBER = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# A month word only counts as a date when it is being used as one. "may I have my invoice"
# is not a request for May. Each of these pins the word to a year, to a date-shaped
# preposition, or to a reply that is nothing but the month.
_MONTH_YEAR_RE = re.compile(rf"\b({_MONTH_NAMES})\.?,?\s+(20\d{{2}})\b", re.I)
_YEAR_MONTH_RE = re.compile(rf"\b(20\d{{2}})[,\s]+({_MONTH_NAMES})\b", re.I)
_MONTH_IN_DATE_SLOT_RE = re.compile(
    rf"\b(?:for|in|of|from|during|since|covering|period)\s+(?:the\s+)?({_MONTH_NAMES})\b",
    re.I,
)
_MONTH_AS_ADJECTIVE_RE = re.compile(
    rf"\b(?:the\s+)?({_MONTH_NAMES})\s+(?:invoice|statement|summary|usage|charges|bill)\b",
    re.I,
)
_MONTH_ONLY_RE = re.compile(
    rf"^\W*({_MONTH_NAMES})\.?\W*(?:please|thanks|thank you)?\W*$", re.I
)
_RELATIVE_MONTH = (
    (("this month", "current month"), 0),
    (("last month", "previous month", "past month"), -1),
)


def _month_number(word: str) -> int:
    return _MONTH_NUMBER[word.lower()[:3]]


def _shift_month(today: date, months: int) -> str:
    year, month = _add_months(today.year, today.month, months)
    return f"{year:04d}-{month:02d}"


def _normalise_period(value: Any, today: date) -> str | None:
    """Turn a period the user actually named into the YYYY-MM the tools take.

    "the March invoice" reached the renderer as period="March", which no query can use —
    it either errors or matches nothing and produces an empty statement that still looks
    like an invoice. People say months by name, so the name is converted here.

    What it will not do is read a month out of a sentence that was not about time. The
    year has to sit beside the month, too: scanning the whole transcript for any `20\\d\\d`
    turned "the Cryo-EM was installed in 2019" into an invoice for March 2019.

    A bare month with no year means the most recent one that has already happened: asked
    in August for "the March invoice", nobody means the March that has not arrived yet.
    """
    if value in (None, ""):
        return None
    text_ = str(value).strip()
    exact = _PERIOD_RE.search(text_)
    if exact:
        return exact.group(0)

    low = text_.lower()
    for phrases, offset in _RELATIVE_MONTH:
        if any(phrase in low for phrase in phrases):
            return _shift_month(today, offset)

    # A year stated next to the month is the user's own; anything further away is not.
    paired = _MONTH_YEAR_RE.search(low)
    if paired:
        return f"{int(paired.group(2)):04d}-{_month_number(paired.group(1)):02d}"
    paired = _YEAR_MONTH_RE.search(low)
    if paired:
        return f"{int(paired.group(1)):04d}-{_month_number(paired.group(2)):02d}"

    for pattern in (_MONTH_IN_DATE_SLOT_RE, _MONTH_AS_ADJECTIVE_RE, _MONTH_ONLY_RE):
        found = pattern.search(low)
        if not found:
            continue
        month = _month_number(found.group(1))
        year = today.year if month <= today.month else today.year - 1
        return f"{year:04d}-{month:02d}"
    return None


def require_document_period(
    plan: dict[str, Any], message: str, history: str
) -> dict[str, Any]:
    """Never render a dated document for a date the user did not give.

    Asked bare for "my invoice", the planner filled in a period on its own — usually the
    current month — and the user got a finished PDF for a window they never chose. It
    reads as an answer, not a guess, which is what makes it dangerous: an invoice is only
    a fact about the period on it.

    So the period must be traceable to something the conversation actually says, and it
    is converted into the form the tools take. When nothing refers to a time at all, this
    returns a question instead of a call.
    """
    today = datetime.now(UTC).date()

    if plan.get("tool") == "generate_document":
        arguments = (
            plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {}
        )
        template = str(arguments.get("template") or "")
        required = _PERIOD_REQUIRED.get(template)
        if required is None:
            return plan
        key, question = required
        params = dict(arguments.get("params") or {})
        # Deliberately ignores whatever the planner put in params. Asked bare for "my
        # invoice" it supplies the current month with total confidence, and a period that
        # came from the model rather than from the user is exactly the invented fact this
        # guard exists to stop.
        period = _grounded_period(message, history, today)
        if period:
            return {
                **plan,
                "arguments": {**arguments, "params": {**params, key: period}},
            }
        return _ask_for_period(question, key, plan, template)

    # Only when the planner produced no call at all. A complete booking or service request
    # is not a document request that forgot its date — asking "which month?" in reply to
    # "book me the cryo-EM next week for my usage" abandons a request we understood.
    if plan.get("tool"):
        return plan
    for template, pattern in _DOCUMENT_SUBJECTS:
        if template not in _PERIOD_REQUIRED or not pattern.search(message):
            continue
        key, question = _PERIOD_REQUIRED[template]
        if _grounded_period(message, history, today):
            return plan
        return _ask_for_period(question, key, plan, template)
    return plan


def _grounded_period(message: str, history: str, today: date) -> str | None:
    """The period the conversation names, read in the order it should be believed.

    Three separate bugs lived in scanning one flat blob of message-plus-history:

    * the first match in the blob won, so an older turn beat the month the user had just
      typed — "now give me the July invoice" rendered March because March was further up;
    * "convert it to a pdf" picked the earliest invoice in the thread rather than the one
      on screen, for the same reason;
    * the guard's own clarifying question counted as evidence.

    So the current message is asked first, then earlier turns from the most recent
    backwards, and our own clarifications are not evidence of anything.
    """
    for segment in _grounding_segments(message, history):
        period = _normalise_period(segment, today)
        if period:
            return period
    return None


# Lines as `graph.format_history` writes them. Coupled to that format on purpose: the
# alternative is threading structured history through four call sites to reach one guard.
_HISTORY_LINE_RE = re.compile(r"^\s*(user|assistant)\s*(?:\(([^)]*)\))?:\s*(.*)$")


def _grounding_segments(message: str, history: str) -> list[str]:
    """The message, then earlier turns most-recent-first, minus our own questions."""
    segments = [message]
    earlier: list[str] = []
    for line in (history or "").splitlines():
        found = _HISTORY_LINE_RE.match(line)
        if not found:
            continue
        speaker, kind, said = found.group(1), found.group(2) or "", found.group(3)
        if speaker == "assistant" and kind.strip() == "clarify":
            continue
        if said.strip():
            earlier.append(said)
    segments.extend(reversed(earlier))
    return segments


def _ask_for_period(
    question: str, key: str, withheld: dict[str, Any], template: str
) -> dict[str, Any]:
    log.info("asking for the period before rendering %s", template)
    return {
        "tool": None,
        "ask": question,
        "missing": key,
        "why": "the document is defined by its period and the conversation names none",
        "withheld": withheld,
    }


def _answering_a_document_question(history: str) -> bool:
    """Did we just ask something, about a document, that this message answers?

    Only the immediately preceding turn, and only a clarify: a question two turns back has
    been overtaken, and any other response type was not a question at all.
    """
    last_clarify = None
    for line in (history or "").splitlines():
        found = _HISTORY_LINE_RE.match(line)
        if not found:
            continue
        speaker, kind, said = found.group(1), (found.group(2) or "").strip(), found.group(3)
        if speaker == "assistant":
            last_clarify = said if kind == "clarify" else None
    if not last_clarify:
        return False
    return any(pattern.search(last_clarify) for _, pattern in _DOCUMENT_SUBJECTS)


_ACCOUNT_RE = re.compile(r"\bACC-[A-Z0-9]+\b")
_PERIOD_RE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])\b")
_BOOKING_RE = re.compile(r"\bbk-[a-z0-9]+\b", re.I)

# What each document cannot be rendered without. Checked before proposing, so the card a
# human approves is one that can actually produce a file.
_MISSING_FOR = {
    "invoice_statement": ("account_code", "period"),
    "booking_confirmation": ("booking_id",),
}


def _document_params_from(template: str, text_: str) -> dict[str, Any]:
    """Recover a document's parameters from what the conversation already showed.

    Every value comes from the transcript — the invoice the user was just shown carries
    its own account code and period — so nothing here invents a parameter.
    """
    if template == "invoice_statement":
        account = _ACCOUNT_RE.search(text_)
        # _normalise_period, not the bare YYYY-MM pattern: the conversation says "the
        # March 2026 invoice" far more often than "2026-03", and reading only the strict
        # form left the period looking absent. The required-params check then failed and
        # the planner's own (wrong) template survived the correction meant to replace it.
        period = _normalise_period(text_, datetime.now(UTC).date())
        return {
            **({"account_code": account.group(0)} if account else {}),
            **({"period": period} if period else {}),
        }
    if template == "usage_summary":
        period = _PERIOD_RE.search(text_)
        return {"month": period.group(0)} if period else {}
    if template == "booking_confirmation":
        booking = _BOOKING_RE.search(text_)
        return {"booking_id": booking.group(0)} if booking else {}
    return {}


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


def require_supplied_fields(plan: dict[str, Any], source: str) -> dict[str, Any]:
    """A service request's field values must come from what the user put in front of us.

    Same discipline as require_supplied_identity, for the same reason. Asked to submit a
    form that had not been uploaded, the planner proposed sample_count=15 — a turnaround
    time from the public policy text — then, once that text was fenced off, sample_count=12
    and 24, from nowhere at all. The prompt already said not to invent; this checks. A
    fabricated sample count on an approval card is indistinguishable from a real one, and
    the approver has no way to know it was never on any form.

    Only values with substance are checked: numbers, and words of three or more letters.
    Enum choices are exempt when they appear in the template's own options — "150bp" is
    picked from a list, not read off a document. `source` is the message, the conversation
    and only the caller's OWN documents; shared policy text is deliberately not part of it,
    because a policy is not something the user filled in.
    """
    if plan.get("tool") != "create_service_request":
        return plan
    arguments = plan.get("arguments")
    if not isinstance(arguments, dict):
        return plan
    fields = arguments.get("fields")
    if not isinstance(fields, dict) or not fields:
        return plan

    haystack = re.sub(r"\s+", " ", source.lower())
    ungrounded: list[str] = []
    for name, value in fields.items():
        if value is None or isinstance(value, bool):
            continue
        text_ = re.sub(r"\s+", " ", str(value).strip().lower())
        if not text_:
            continue
        # A number is either on the page or it is invented. A short token ("no", "yes",
        # "150bp") is not enough on its own to prove anything, so it is not asked to.
        if re.fullmatch(r"[\d.,]+", text_):
            if not re.search(rf"(?<![\d.]){re.escape(text_.rstrip('.'))}(?![\d])", haystack):
                ungrounded.append(name)
        elif len(text_) >= 3 and text_ not in haystack:
            # Whole phrase absent; accept if every substantive word is present, which
            # covers "Mus musculus" written as "mus musculus (mouse)".
            words = [w for w in re.findall(r"[a-z0-9]+", text_) if len(w) >= 3]
            if words and not all(w in haystack for w in words):
                ungrounded.append(name)
    if not ungrounded:
        return plan

    log.warning("service request invented field(s) %s; asking instead", ungrounded)
    return {
        "tool": None,
        "missing": ungrounded,
        "ask": (
            "I can only submit values you have actually given me. I do not have "
            + ", ".join(ungrounded)
            + " from you — could you tell me, or upload the filled form?"
        ),
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
    chosen = apply_stated_format(chosen, message)
    chosen = carry_forward_document_subject(chosen, message, history)
    chosen = apply_relative_date(chosen, message)
    chosen = carry_forward_instrument(chosen, message, history)
    chosen = require_document_period(chosen, message, history)
    # The identity guard may read any document the caller can see: a name on a shared
    # roster is still a real person. The fields guard reads only what the caller wrote
    # down themselves — the whole point of it.
    own_documents = documents.split("SHARED POLICY AND REFERENCE TEXT", 1)[0]
    chosen = require_supplied_fields(chosen, f"{message}\n{history}\n{own_documents}")
    return require_supplied_identity(chosen, f"{message}\n{history}\n{documents}")


# An acknowledgement, said outright: "the PI has approved it", "I am the PI and I
# acknowledge this", "signed off by the PI".
_PI_ACK_RE = re.compile(
    r"\bpi\b[^.?!]{0,50}?\b(?:acknowledg\w*|approv\w*|agreed|consent\w*|confirm\w*"
    r"|sign(?:ed)?[ -]?off|ok(?:ay|'?d)?)\b"
    r"|\b(?:acknowledg\w*|approv\w*|confirm\w*|consent\w*|sign(?:ed)?[ -]?off)\b"
    r"[^.?!]{0,50}?\bpi\b"
    r"|\bi\s+acknowledge\b|\bi\s+am\s+the\s+pi\b|\bas\s+(?:the\s+)?pi\b",
    re.IGNORECASE,
)
# Or said in answer to our own question, where "yes" is the whole sentence.
_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|correct|confirmed|indeed|they have|he has|she has|"
    r"i have|i do|go ahead|that'?s right|of course|sure)\b",
    re.IGNORECASE,
)
_WE_ASKED_FOR_PI_ACK = "has the pi acknowledged"


def _pi_has_acknowledged(message: str, history: str, plan: dict) -> bool:
    """Did the caller actually say the PI acknowledges this, in words?

    Not "is pi_ack true in the plan" — that is the model's opinion of the conversation,
    and on an onboarding request phrased no differently it came back true one run and
    false the next. The plan's flag is necessary but never sufficient: something the
    caller said has to back it.
    """
    if not (plan.get("arguments") or {}).get("pi_ack"):
        return False
    said = f"{history}\n{message}"
    if _PI_ACK_RE.search(said):
        return True
    # "Yes." on its own means yes only when the previous turn was us asking this.
    return bool(
        _AFFIRMATIVE_RE.search(message)
        and _WE_ASKED_FOR_PI_ACK in history.lower()
    )


_BOOKING_TOOLS = frozenset({"cancel_booking", "reschedule_booking"})
_BOOKING_ID_RE = re.compile(r"\bbk-[a-z0-9]+\b", re.IGNORECASE)


def open_bookings_of(ctx: Ctx) -> list[dict[str, Any]]:
    """The caller's bookings that could still be cancelled or moved, soonest first."""
    with session_scope() as s:
        return [
            dict(row)
            for row in s.execute(
                text(
                    """SELECT b.id, b.starts_at, b.status, i.name AS instrument
                       FROM infinity.bookings b
                       JOIN infinity.instruments i ON i.id = b.instrument_id
                       WHERE b.user_id = :uid
                         AND b.status NOT IN ('cancelled', 'completed')
                         AND b.starts_at > now()
                       ORDER BY b.starts_at"""
                ),
                {"uid": ctx.user_id},
            ).mappings().all()
        ]


# A time the caller actually settled — a clock time, a date, or a day the conversation
# can resolve. Deliberately generous: this only decides whether to ASK, and asking someone
# who already told us is the annoying failure.
_WHEN_STATED_RE = re.compile(
    r"\b\d{1,2}\s*(?::\d{2}|[ap]\.?m\.?|o'?clock)"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:today|tonight|tomorrow|yesterday|now|asap|this (?:morning|afternoon|evening))\b"
    r"|\b(?:mon|tues?|wed(?:nes)?|thur?s?|fri|sat(?:ur)?|sun)(?:day)?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b(?:next|this|coming)\s+\w+"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b",
    re.IGNORECASE,
)


def _when_was_never_said(plan: dict, message: str, history: str) -> AgentResponse | None:
    """Ask when, rather than invent a slot and then refuse it.

    "Can I book Confocal C2" names no day and no time. The planner supplied today at
    10:00 regardless — booking, on one occasion, a slot that had begun ten hours earlier.
    The past-start check in the tool now stops that, but stopping it produces its own
    confusion: the caller is told that a start time they never chose has already passed.

    Asked against the whole thread, because "is C2 free on 2 April 2027?" then "book it
    from 9am" is a caller who has said when — in two turns, which is still saying it.
    """
    if plan.get("tool") != "request_booking":
        return None
    if not (plan.get("arguments") or {}).get("starts_at"):
        return None
    if _WHEN_STATED_RE.search(f"{history}\n{message}"):
        return None
    log.info("booking planned with a time nobody gave; asking when")
    return AgentResponse(
        response_type="clarify",
        route="action",
        text=("When would you like it? Give me a day and a start time — the cores are "
              "open 08:00–20:00 UTC, Monday to Friday."),
        meta={"plan": plan, "awaiting": "starts_at"},
    )


def _started_bookings_of(ctx: Ctx) -> list[dict[str, Any]]:
    """Bookings that are live or lately past — not cancelled, not completed, already begun.

    The counterpart to open_bookings_of, and the reason the "nothing to change" message
    can tell a caller which of the two situations they are in.
    """
    with session_scope() as s:
        return [
            dict(row)
            for row in s.execute(
                text(
                    """SELECT b.id, b.starts_at, b.status, i.name AS instrument
                       FROM infinity.bookings b
                       JOIN infinity.instruments i ON i.id = b.instrument_id
                       WHERE b.user_id = :uid
                         AND b.status NOT IN ('cancelled', 'completed')
                         AND b.starts_at <= now()
                       ORDER BY b.starts_at DESC"""
                ),
                {"uid": ctx.user_id},
            ).mappings().all()
        ]


def _with_the_booking_being_discussed(
    plan: dict, message: str, history: str, ctx: Ctx
) -> dict | None:
    """The same change, pointed at a booking that actually exists.

    "Can I reschedule to 08:00 to 12:00" — one turn after booking the Bioanalyzer —
    planned reschedule_booking with an instrument_id and no booking_id at all, and was
    refused as "That lookup does not take an instrument"; a neighbouring run supplied an
    id that did not exist and was refused as "No such booking. Check the identifier and
    try again." Both read as the platform having lost the booking it had just confirmed.

    Resolved the way a person would: an id named in this thread if it is really theirs,
    otherwise the one booking they have open. Validated against their own records rather
    than against the text, so an action id quoted back from an approval card cannot pass
    as a booking id just by having been mentioned.
    """
    if plan.get("tool") not in _BOOKING_TOOLS:
        return None
    arguments = plan.get("arguments") or {}
    open_bookings = open_bookings_of(ctx)
    theirs = {row["id"].lower(): row["id"] for row in open_bookings}

    given = str(arguments.get("booking_id") or "").strip().lower()
    if given in theirs:
        return None

    # Most recently mentioned first: a thread that named two bookings means the one just
    # discussed, not the one from six turns ago.
    for candidate in reversed(_BOOKING_ID_RE.findall(f"{history}\n{message}")):
        if candidate.lower() in theirs:
            log.info("resolved the booking from the conversation: %s", candidate)
            return {**plan, "arguments": {**arguments, "booking_id": theirs[candidate.lower()]}}

    if len(open_bookings) == 1:
        only = open_bookings[0]["id"]
        log.info("the caller has exactly one open booking (%s); using it", only)
        return {**plan, "arguments": {**arguments, "booking_id": only}}
    return None


def _which_booking(plan: dict, message: str, history: str, ctx: Ctx) -> AgentResponse | None:
    """Ask which booking to change, naming them, when the thread does not settle it."""
    if plan.get("tool") not in _BOOKING_TOOLS:
        return None
    open_bookings = open_bookings_of(ctx)
    if str((plan.get("arguments") or {}).get("booking_id") or "").strip().lower() in {
        row["id"].lower() for row in open_bookings
    }:
        return None
    if not open_bookings:
        # "You have no upcoming bookings" is true and reads as a denial of the booking
        # they made four turns ago. Someone holding a slot that started this morning is
        # not someone with no bookings, and telling them so sounds like the record was
        # lost. Say which of the two it is.
        started = _started_bookings_of(ctx)
        if started:
            soonest = started[0]
            return AgentResponse(
                response_type="redirect",
                route="action",
                text=(
                    f"Your {soonest['instrument']} booking on "
                    f"{soonest['starts_at']:%-d %B at %H:%M} UTC has already started, and "
                    "the cancellation rules cover notice given before the start — so this "
                    "one is the core facility admin's to unwind, not mine."
                ),
                meta={"plan": plan, "already_started": [b["id"] for b in started]},
            )
        return AgentResponse(
            response_type="redirect",
            route="action",
            text=("You have no bookings open to change — nothing upcoming, and nothing "
                  "still running. A completed or cancelled booking cannot be moved."),
            meta={"plan": plan},
        )
    listed = "; ".join(
        f"{row['instrument']} on {row['starts_at']:%Y-%m-%d %H:%M} UTC ({row['id']})"
        for row in open_bookings[:5]
    )
    return AgentResponse(
        response_type="clarify",
        route="action",
        text=f"Which booking do you mean — {listed}?",
        meta={"plan": plan, "awaiting": "booking_id",
              "options": [row["id"] for row in open_bookings[:5]]},
    )


def _without_arguments_the_tool_does_not_take(plan: dict) -> dict | None:
    """Drop arguments the tool has no parameter for, when what is left can still run.

    The read branch already does this on error. A write never got the chance: the planner
    put instrument_id on reschedule_booking and the signature error went to the user as
    "That lookup does not take an instrument."
    """
    name = plan.get("tool") or ""
    accepted = tools_mod.accepted_arguments(name)
    arguments = plan.get("arguments") or {}
    if not accepted or not set(arguments) - accepted:
        return None
    kept = {k: v for k, v in arguments.items() if k in accepted}
    if tools_mod.required_arguments(name) - set(kept):
        return None
    log.info("dropping %s that %s does not take", sorted(set(arguments) - accepted), name)
    return {**plan, "arguments": kept}


def account_codes_of(ctx: Ctx) -> list[str]:
    """The account codes this caller may charge to, in the order the record lists them."""
    with session_scope() as s:
        codes = s.execute(
            text("SELECT account_codes FROM infinity.users WHERE id = :id"),
            {"id": ctx.user_id},
        ).scalar_one_or_none()
    return [str(code) for code in (codes or [])]


def _needs_an_account_code(plan: dict) -> bool:
    """A write that takes an account code and was planned without one."""
    name = plan.get("tool") or ""
    if "account_code" not in tools_mod.accepted_arguments(name):
        return False
    return not (plan.get("arguments") or {}).get("account_code")


def _with_the_callers_only_account_code(plan: dict, ctx: Ctx) -> dict | None:
    """The same write, charged to the one account the caller actually has.

    "Book Confocal C2 on 2 April 2027 from 3am to 5am" was planned with no account code,
    and the tool refused it — "account_code is required" — before the booking's real
    problem (3am is outside opening hours) was ever reached. The caller was asked for the
    only value they could possibly have given.

    Only when there is exactly one: with a choice to make it is theirs, and with none
    there is nothing to fill. Not a guess either way — and the code appears on the
    approval card, so nothing is charged anywhere before they have read it.
    """
    if not _needs_an_account_code(plan):
        return None
    codes = account_codes_of(ctx)
    if len(codes) != 1:
        return None
    log.info("filling the caller's only account code %s", codes[0])
    return {**plan, "arguments": {**(plan.get("arguments") or {}),
                                  "account_code": codes[0]}}


def _which_account_code(plan: dict, ctx: Ctx) -> AgentResponse | None:
    """Ask which account to charge, when the caller has more than one to choose from."""
    if not _needs_an_account_code(plan):
        return None
    codes = account_codes_of(ctx)
    if len(codes) < 2:
        return None
    return AgentResponse(
        response_type="clarify",
        route="action",
        text=f"Which account should I charge — {' or '.join(codes)}?",
        meta={"plan": plan, "awaiting": "account_code", "options": codes},
    )


def propose(
    message: str, ctx: Ctx, thread_id: str | None = None, history: str = ""
) -> AgentResponse:
    """Create the pending action and return the approval request."""
    chosen = plan(message, ctx, history)
    name = chosen.get("tool")

    if name in tools_mod.WRITE_TOOLS:
        # Which booking first, and by the strongest check available: matched against the
        # caller's own open bookings rather than against the words on screen. An action id
        # quoted back from an approval card is "mentioned in the thread" and is still not
        # a booking id. When this resolves, the id is real by construction and the
        # invented-identifier guard below has nothing left to judge.
        if resolved := _with_the_booking_being_discussed(chosen, message, history, ctx):
            chosen = resolved
        elif ask := _which_booking(chosen, message, history, ctx):
            return ask

        if ask := _when_was_never_said(chosen, message, history):
            return ask

    # An identifier nobody gave is not an identifier. "Where is my sample?" planned
    # track_sample(sample_id="s-12345") — a placeholder shaped like the real thing — and
    # the tool refused it as a record that never existed, which reads to the caller as the
    # platform being broken about their data rather than as the assistant having made one
    # up. The read branch asks instead of guessing; a write, where the guess would be
    # acted on, has more reason to and not less.
    # Booking tools are exempt: the block above matched their id against the caller's own
    # open bookings, which is a stronger test than "did these characters appear on
    # screen", and a booking resolved from context is correct precisely BECAUSE the caller
    # never typed it.
    if name in tools_mod.WRITE_TOOLS and name not in _BOOKING_TOOLS:
        # Imported here for the same reason data imports this module lazily: neither
        # branch should have to load the other to be importable.
        from server.agent.data import _ID_IN_ENGLISH, _invented_record_id

        if invented := _invented_record_id(chosen, message, history):
            log.info("write plan invented %s=%r; asking instead", invented,
                     (chosen.get("arguments") or {}).get(invented))
            return AgentResponse(
                response_type="clarify",
                route="action",
                text=(f"Tell me {_ID_IN_ENGLISH[invented]} and I'll prepare that for "
                      "your approval. I won't act on a record id I had to guess at."),
                meta={"plan": chosen, "awaiting": invented},
            )

        # Onboarding needs the PI to have actually said yes.
        #
        # Two ways to get this wrong and the dangerous one is not the obvious one. The
        # planner sent pi_ack=false, which the tool refused as "pi_ack must be true" — a
        # field name and a boolean where a question belonged, and merely unhelpful. It
        # also, on the same request phrased the same way, sent pi_ack=TRUE with nothing
        # in the conversation acknowledging anything: a consent recorded against a PI who
        # had not given it, which is the failure this whole approval mechanism exists to
        # prevent. SYSTEM says being a PI is not the same as having said so; the planner
        # reads the caller's role and concludes otherwise.
        #
        # So the acknowledgement is checked against what was actually said, in either
        # direction, and asked for when it was not.
        if name == "create_onboarding_request" and not _pi_has_acknowledged(
            message, history, chosen
        ):
            log.info("onboarding planned without pi_ack; asking for it")
            return AgentResponse(
                response_type="clarify",
                route="action",
                text=("Has the PI acknowledged this new user? Onboarding needs that "
                      "before I can prepare it — tell me they have and I'll put it up "
                      "for approval."),
                meta={"plan": chosen, "awaiting": "pi_ack"},
            )

    # Every write, booking tools included: the planner put instrument_id on
    # reschedule_booking and the signature error went to the caller as "That lookup does
    # not take an instrument."
    if name in tools_mod.WRITE_TOOLS:
        if trimmed := _without_arguments_the_tool_does_not_take(chosen):
            chosen = trimmed

        if repaired := _with_the_callers_only_account_code(chosen, ctx):
            chosen = repaired
        elif ask := _which_account_code(chosen, ctx):
            return ask

    if name not in tools_mod.WRITE_TOOLS:
        # Missing information the user alone can supply: ask, rather than inventing it
        # or firing a call that the tool will only reject.
        question = str(chosen.get("ask") or "").strip()
        if question:
            # A question back to the user is a clarification, not a refusal. It was typed
            # as a redirect, so the UI — which turns "Which one — A or B?" into clickable
            # options — never saw the case it was built for, and the reader was told the
            # request had failed when in fact one word would complete it.
            return AgentResponse(
                response_type="clarify",
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

    progress.emit("prepared the change — nothing has happened yet")
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
            # The preview already ends in a full stop when it carries a policy sentence —
            # "...charged at 50% of the booked time.." went out with two.
            f"I've prepared this, but I haven't done it. "
            f"{str(pending['payload_preview']).rstrip('.')}.\n\n"
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
        # A path on the server is not something the reader can act on; the route that
        # serves the file is. Size and format stay, because they tell them what they are
        # about to open.
        size_kb = max(1, round(result.get("bytes", 0) / 1024))
        return (
            f"{result['title']} is ready — {result.get('format', 'file').upper()}, "
            f"about {size_kb} kB. Download it here: {result.get('download_url', '')}"
        ).strip()
    return f"Done. The action completed with status '{status}'."

# Versioned by content hash — see server/agent/prompts.py.
VERSION_SYSTEM = register("action.system", SYSTEM)
