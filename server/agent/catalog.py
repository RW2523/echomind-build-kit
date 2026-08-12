"""The source catalog, and the relevance gate that reads it.

The knowledge branch has a confidence gate that refuses to answer from documents it
cannot verify. This is its counterpart on the data branch: a registry describing every
source of records the assistant can read, and two deterministic checks around the lookup
— one BEFORE it (can anything here answer this, and may this caller see it?) and one
AFTER it (did the rows actually answer it?).

Whatever already lives in the tool registry is derived from it rather than retyped: a
source's purpose is its ToolSpec description, and the lowest role it admits is read out
of its tier string. A tool that changes tier therefore changes its catalog entry in the
same commit, instead of leaving this file quietly describing the old one. What cannot be
derived — which subject a source answers about, how far its scope reaches, the words
people actually use for it — is declared here beside the source it belongs to.

Both checks are keyword matching and integer comparisons, never a model call. A gate that
asked an LLM whether a question was answerable would cost more than the lookup it guards
and would change its mind between runs; this one is the same every time and can be
tested. It also fails OPEN by design: when a question is too short or too oblique to
judge — "and in April?" — it lets the question through. A wrong refusal costs a user an
answer they were entitled to and looks like a broken product; a wrong pass costs one
lookup that the scope guards in data.py and the after-check both still stand behind.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from server.auth import Ctx
from server.mcp import sql_guard
from server.mcp import tools as tools_mod

# The nine things a caller can ask this platform about. A source that answers about none
# of them is a source the gate can never reach, so every entry names at least one.
SUBJECTS = (
    "bookings", "usage", "billing", "samples", "requests", "projects",
    "instruments", "facilities", "training",
)

# How the subjects read in a sentence, for the refusal that lists what we do cover.
SUBJECTS_IN_ENGLISH = (
    "bookings, usage hours, invoices and charges, service requests, samples, projects, "
    "instruments, facilities and training records"
)

SCOPES = ("own records", "own labs", "everything")

ROLE_RANK = {"user": 1, "pi": 2, "admin": 3}

# Tier T0/T1 answer for the caller themselves, so any authenticated role reaches them;
# T2 is the PI ladder and T3 is admin-only. Spec 05's ladder, expressed once.
_TIER_ROLE = {"0": "user", "1": "user", "2": "pi", "3": "admin"}
_TIER_RE = re.compile(r"T([0-3])")


def _min_role(tier: str) -> str:
    """The lowest role a tier string admits, e.g. "T1 self / T2 lab / T3" -> "user".

    Derived rather than declared because a tier and a minimum role are the same fact:
    writing it twice is writing it wrong eventually. An unparseable tier is treated as
    admin-only, so a typo closes a source rather than opening it.
    """
    tiers = _TIER_RE.findall(tier or "")
    return _TIER_ROLE[min(tiers)] if tiers else "admin"


@dataclass(frozen=True)
class Source:
    """One queryable source: what it answers about, who may read it, and how far.

    `fields` maps what the source returns to what it means in plain English, so a caller
    of this registry never has to read the SQL to know what a column is.
    """

    name: str
    kind: str  # "tool" | "view"
    purpose: str
    subjects: tuple[str, ...]
    fields: dict[str, str]
    min_role: str
    scope: str
    keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- what people call each subject -------------------------------------------------

# Deliberately generous. Every word here is one more question the gate lets through, and
# the cost of an extra word is at worst a lookup that returns nothing — which the
# after-check answers honestly — while the cost of a missing word is a refusal to a user
# whose question we could have answered.
SUBJECT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bookings": (
        "booking", "bookings", "booked", "book", "reservation", "reservations",
        "reserved", "slot", "slots", "session", "sessions", "scheduled", "schedule",
        "calendar", "diary", "availability", "available", "free", "busy", "upcoming",
        "cancelled", "canceled", "no-show",
    ),
    "usage": (
        "usage", "used", "hours", "hour", "runtime", "run time", "machine time",
        "instrument time", "utilisation", "utilization", "tracked", "logged", "time on",
        "how long", "downtime",
    ),
    "billing": (
        "billing", "bill", "billed", "invoice", "invoices", "invoiced", "charge",
        "charged", "charges", "cost", "costs", "spend", "spent", "spending", "price",
        "priced", "rate", "rates", "amount", "budget", "account code", "account codes",
        "fee", "fees", "payment", "money", "expensive", "total",
    ),
    "samples": (
        "sample", "samples", "barcode", "barcodes", "specimen", "specimens", "aliquot",
        "tube", "tubes", "batch",
    ),
    "requests": (
        "request", "requests", "requested", "service request", "ticket", "tickets",
        "submission", "submitted", "status", "progress", "queue",
    ),
    "projects": (
        "project", "projects", "allocation", "allocations", "collaborators",
        "project members",
    ),
    "instruments": (
        "instrument", "instruments", "microscope", "microscopes", "sequencer",
        "sequencers", "spectrometer", "machine", "machines", "equipment", "device",
        "confocal", "cryo-em", "titan", "novaseq", "miseq", "orbitrap", "nanopore",
        "maldi", "qtof", "light sheet", "lightsheet", "modality", "technique",
        "techniques", "rna-seq", "sequencing", "proteomics", "metabolomics",
        "single particle analysis", "maintenance", "offline", "broken", "working",
        "hourly rate", "sample type", "sample types",
    ),
    "facilities": (
        "facility", "facilities", "core", "cores", "campus", "building", "room",
        "address", "opening hours", "where is", "located", "location", "contact",
        "imaging core", "genomics core", "mass spectrometry",
    ),
    "training": (
        "training", "trained", "certification", "certified", "certificate",
        "competency", "qualified", "induction", "biosafety", "sign-off", "signed off",
        "authorised", "authorized", "inducted",
    ),
}


def _word_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """Whole-word alternation, so "core" never fires inside "score"."""
    alternatives = "|".join(re.escape(w) for w in sorted(set(words)))
    return re.compile(rf"\b(?:{alternatives})\b")


_SUBJECT_RE = {subject: _word_pattern(words) for subject, words in SUBJECT_KEYWORDS.items()}


def subjects_in(text: str) -> tuple[str, ...]:
    """Which catalogued subjects this text talks about, in SUBJECTS order."""
    lowered = (text or "").lower()
    return tuple(s for s in SUBJECTS if _SUBJECT_RE[s].search(lowered))


# --- the read tools ----------------------------------------------------------------

# Purpose, tier and therefore minimum role come from ToolSpec. Only what ToolSpec cannot
# know is written here: the subject each tool answers about, how far it reaches, the
# words that point at it, and what its result means to a reader.
#
# `scope` is the reach a caller at the tool's MINIMUM role gets. get_usage_records is
# "own records" because that is what a plain user gets from it; a PI's wider reach is a
# property of the PI, not of the tool, and the tool enforces it either way.
_READ_TOOL_FACTS: dict[str, dict[str, Any]] = {
    "get_user_profile": {
        "subjects": ("training", "billing"),
        "scope": "own records",
        "keywords": ("profile", "my role", "my lab", "who am i", "email"),
        "fields": {
            "user_id": "which person the profile describes",
            "name": "their full name",
            "email": "their email address",
            "role": "user, pi or admin",
            "lab": "the lab they belong to",
            "training": "each course they hold, and whether it is current",
            "account_codes": "the codes they may charge work to",
        },
    },
    "get_facility_catalog": {
        "subjects": ("facilities", "instruments"),
        "scope": "everything",
        "keywords": ("catalogue", "catalog", "what instruments", "list of instruments"),
        "fields": {
            "facilities": "each facility, with its short code",
            "instruments": "each instrument, its facility, hourly rate and status",
            "templates": "the service request forms a facility accepts",
        },
    },
    "check_availability": {
        "subjects": ("bookings", "instruments"),
        "scope": "everything",
        "keywords": ("free on", "any time"),
        "fields": {
            "free_slots": "each window in which nothing is booked",
            "requested_window_free": "whether the window asked about is clear",
            "bookable": "whether the instrument can be booked at all",
            "unavailable_reason": "why it cannot be booked, when it cannot",
            "conflicting_bookings": "how many bookings overlap the window",
            "opening_hours": "the hours the facility publishes",
        },
    },
    "get_my_bookings": {
        "subjects": ("bookings",),
        "scope": "own records",
        "keywords": ("my bookings", "i have booked"),
        "fields": {
            "instrument": "what was booked",
            "facility": "where it lives",
            "starts_at": "when the booking begins",
            "ends_at": "when it ends",
            "status": "confirmed, cancelled or completed",
            "account_code": "the code it is charged to",
            "count": "how many bookings the filter matched",
        },
    },
    "get_usage_records": {
        "subjects": ("usage",),
        "scope": "own records",
        "keywords": ("scheduled versus tracked", "actual hours", "booked hours"),
        "fields": {
            "instrument": "which instrument the hours were on",
            "month": "the month the hours fall in",
            "scheduled_hours": "hours the booking calendar reserved",
            "tracked_hours": "hours the instrument actually recorded",
            "difference_hours": "tracked hours minus scheduled hours",
        },
    },
    "get_request_status": {
        "subjects": ("requests", "samples"),
        "scope": "own records",
        "keywords": ("my requests", "where has it got to"),
        "fields": {
            "template": "which service was requested",
            "status": "where the request has got to",
            "history": "each state it has passed through, and when",
            "samples": "the samples the request produced",
        },
    },
    "track_sample": {
        "subjects": ("samples",),
        "scope": "own records",
        "keywords": ("where is sample", "sample state", "timeline"),
        "fields": {
            "barcode": "the label on the tube",
            "state": "the stage the sample has reached",
            "updated_at": "when it last moved",
            "request_status": "the state of the request it came from",
            "timeline": "every step so far, oldest first",
        },
    },
    "get_billing_summary": {
        "subjects": ("billing",),
        "scope": "own records",
        "keywords": ("my invoice", "what do i owe", "line items"),
        "fields": {
            "account_code": "the code the invoice is against",
            "period": "the month the invoice covers",
            "total": "the invoice total",
            "line_count": "how many lines make it up",
            "lines": "each charge, what it was for and how much",
        },
    },
    "get_project_overview": {
        "subjects": ("projects",),
        "scope": "own labs",
        "keywords": ("who is on", "members of"),
        "fields": {
            "project": "the project's name and currency",
            "members": "who is on it, and in what role",
            "cores_used": "the facilities its members have booked",
            "spend_by_lab_period": "spend per member lab per month",
            "spend_total": "the total attributed to the project",
            "spend_basis": "how that spend was attributed, stated plainly",
        },
    },
    "get_instrument_health": {
        "subjects": ("instruments",),
        "scope": "everything",
        "keywords": ("is it working", "out of service", "repair", "repairs"),
        "fields": {
            "instrument": "its name, facility, hourly rate and current status",
            "history": "maintenance events, for admins only",
            "downtime_hours_total": "hours lost to downtime, for admins only",
            "repair_count": "how many repairs, for admins only",
        },
    },
    "find_facilities": {
        "subjects": ("facilities", "instruments"),
        "scope": "everything",
        "keywords": ("nearest", "closest", "near me", "which core", "who can do",
                     "can do", "where can i"),
        "fields": {
            "facilities": "each facility that has an instrument doing this, nearest first",
            "matched": "how many facilities can do it",
            "matched_instruments": "how many instruments behind them can",
            "distance_km": "how far each facility is, when a location was given",
        },
    },
    "recommend_instrument": {
        "subjects": ("instruments",),
        "scope": "everything",
        "keywords": ("which instrument", "what should i use", "best for", "suitable",
                     "recommend", "capable of"),
        "fields": {
            "matches": "the instruments that can do it, best first",
            "why_matched": "what earned each instrument its place",
            "bookable": "whether it can be booked right now, or is down",
            "excluded_by_sample_type": "instruments dropped for not taking that sample",
        },
    },
    "run_readonly_sql": {
        "subjects": ("bookings", "usage", "billing", "instruments"),
        "scope": "own labs",
        "keywords": ("across", "compare", "breakdown", "per lab", "each month"),
        "fields": {
            "rows": "whatever the query selected from the reporting views",
            "columns": "the names of those columns",
            "executed_sql": "the query as it actually ran, after lab scoping",
        },
    },
}


# --- the reporting views -----------------------------------------------------------

# Reachable only through run_readonly_sql, so their minimum role is that tool's — read
# from the same place rather than asserted here a second time.
VIEW_MIN_ROLE = _min_role(tools_mod.TOOLS["run_readonly_sql"].tier)

_VIEW_FACTS: dict[str, dict[str, Any]] = {
    "v_bookings": {
        "purpose": "One row per booking, with who made it and where.",
        "subjects": ("bookings",),
        "scope": "own labs",
        "keywords": ("every booking", "all bookings"),
        "fields": {
            "user_id": "who made the booking",
            "user_name": "their name",
            "lab_id": "the lab they belong to",
            "instrument": "what was booked",
            "facility": "where that instrument lives",
            "starts_at": "when the booking begins",
            "ends_at": "when it ends",
            "status": "confirmed, cancelled or completed",
        },
    },
    "v_usage_summary": {
        "purpose": "Scheduled and tracked hours per user, instrument and month.",
        "subjects": ("usage",),
        "scope": "own labs",
        "keywords": ("hours per month", "per instrument"),
        "fields": {
            "lab_id": "the lab the hours belong to",
            "user_id": "whose hours they are",
            "instrument": "which instrument they were on",
            "month": "the month, as YYYY-MM",
            "scheduled_hours": "hours the calendar reserved",
            "tracked_hours": "hours the instrument recorded",
        },
    },
    "v_billing_lines": {
        "purpose": "One row per invoice line, with the account code it is charged to.",
        "subjects": ("billing",),
        "scope": "own labs",
        "keywords": ("line by line", "what made up"),
        "fields": {
            "account_code": "the code charged",
            "lab_id": "the lab that code belongs to",
            "period": "the month billed, as YYYY-MM",
            "description": "what the charge was for",
            "instrument": "the instrument it was incurred on",
            "amount": "how much, in the invoice currency",
        },
    },
    "v_instrument_downtime": {
        # No lab dimension exists here: downtime belongs to the estate, not to a lab,
        # which is why the SQL validator does not lab-scope this view.
        "purpose": "Downtime hours and repair counts per instrument and month.",
        "subjects": ("instruments",),
        "scope": "everything",
        "keywords": ("most downtime", "out of action"),
        "fields": {
            "instrument": "the instrument that was down",
            "facility": "where it lives",
            "month": "the month, as YYYY-MM",
            "downtime_hours": "hours lost that month",
            "repair_count": "how many of those events were repairs",
        },
    },
}


def _tool_source(name: str) -> Source:
    """A catalog entry for one read tool, derived from its ToolSpec where possible.

    A read tool nobody has described here still gets an entry rather than an import-time
    crash: its subject is inferred from its own name and description with the same
    matcher the gate uses. A registry that takes the API down when someone adds a tool is
    a registry people delete, and the completeness test is the right place to notice.
    """
    spec = tools_mod.TOOLS[name]
    facts = _READ_TOOL_FACTS.get(name)
    if facts is None:
        return Source(
            name=name, kind="tool", purpose=spec.description,
            subjects=subjects_in(f"{name.replace('_', ' ')} {spec.description}"),
            fields=dict(spec.params), min_role=_min_role(spec.tier),
            scope="own records", keywords=tuple(name.split("_")),
        )
    return Source(
        name=name, kind="tool", purpose=spec.description,
        subjects=tuple(facts["subjects"]), fields=dict(facts["fields"]),
        min_role=_min_role(spec.tier), scope=facts["scope"],
        keywords=tuple(facts.get("keywords", ())),
    )


def _view_source(name: str) -> Source:
    """A catalog entry for one reporting view.

    Same rule as the tools: a view the allow-list grants and nobody described is still
    listed, coarsely, rather than quietly missing from a registry that claims to be
    complete. Its columns cannot be guessed, so it declares none and the completeness
    test says so out loud.
    """
    facts = _VIEW_FACTS.get(name)
    if facts is None:
        return Source(
            name=name, kind="view", purpose="A reporting view nobody has described yet.",
            subjects=subjects_in(name.replace("_", " ")), fields={},
            min_role=VIEW_MIN_ROLE, scope="own labs",
        )
    return Source(
        name=name, kind="view", purpose=facts["purpose"],
        subjects=tuple(facts["subjects"]), fields=dict(facts["fields"]),
        min_role=VIEW_MIN_ROLE, scope=facts["scope"],
        keywords=tuple(facts.get("keywords", ())),
    )


# Described views first, in the order the planner is shown them, then anything the
# allow-list grants that this file has not caught up with.
_VIEW_NAMES = [name for name in _VIEW_FACTS if name in sql_guard.ALLOWED_VIEWS] + sorted(
    sql_guard.ALLOWED_VIEWS - set(_VIEW_FACTS)
)

SOURCES: tuple[Source, ...] = tuple(
    [_tool_source(name) for name in tools_mod.READ_TOOLS]
    + [_view_source(name) for name in _VIEW_NAMES]
)

BY_NAME: dict[str, Source] = {s.name: s for s in SOURCES}

_SOURCE_KEYWORD_RE = {
    s.name: _word_pattern(s.keywords) if s.keywords else None for s in SOURCES
}


def view_schema_text() -> str:
    """The four views as `name(column, column, ...)` lines.

    The same rendering the planner is shown, produced from the catalog so a column that
    exists in one and not the other is a test failure rather than a silent disagreement
    between the prompt and this registry.
    """
    return "\n".join(
        f"{s.name}({', '.join(s.fields)})" for s in SOURCES if s.kind == "view"
    )


def sources_for(text: str) -> tuple[Source, ...]:
    """Every catalogued source that could plausibly answer this text.

    A source is a candidate when the text talks about one of its subjects, or uses a
    phrase that points at it specifically.
    """
    lowered = (text or "").lower()
    matched = set(subjects_in(lowered))
    out = []
    for source in SOURCES:
        pattern = _SOURCE_KEYWORD_RE[source.name]
        if matched.intersection(source.subjects) or (pattern and pattern.search(lowered)):
            out.append(source)
    return tuple(out)


# --- the gate ----------------------------------------------------------------------


@dataclass(frozen=True)
class RelevanceResult:
    """The verdict of one gate check: whether to go on, and what was weighed."""

    passed: bool
    reason: str
    considered: tuple[str, ...] = ()
    entitled: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# A lab named in a question: "lab-b", "Lab B". Spelled as the scope guard in data.py
# spells it, because the two must agree on what counts as naming a lab.
_LAB_REF_RE = re.compile(r"\blab[-\s]([a-f])\b", re.IGNORECASE)

# Asking for a lab's figures, rather than for its catalogue. "What instruments are in
# Lab B" is a question anyone may ask; "what did Lab B spend" is not.
_LAB_FIGURES_RE = re.compile(
    r"\b(spen[dt]|charg\w*|cost\w*|bill\w*|invoic\w*|total\w*|usage|used|hours?|"
    r"booking\w*|booked|utili[sz]\w*|budget|allocation)\b",
    re.IGNORECASE,
)

# The caller talking about themselves. Their own records are always a candidate source,
# so a question in these words is never refused for lack of one.
_SELF_REF_RE = re.compile(r"\b(i|me|my|mine|we|us|our|ours)\b", re.IGNORECASE)

# A month or a year the caller named. "2026-03" on its own is a period filter for
# whatever the previous turn was about, and it says nothing about its own subject.
_PERIOD_RE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2})?\b")

_WORD_RE = re.compile(r"[a-z0-9'-]+", re.IGNORECASE)

# Below this, a question carries too few words to be judged on its vocabulary.
MIN_WORDS_TO_JUDGE = 5


def _too_little_to_judge(question: str, history: str) -> bool:
    """True when refusing would be a guess rather than a finding.

    Every case here is one where the words in front of us are not the whole question:
    a follow-up leans on the turn before it, a bare period is a filter, and "my" points
    at records that exist whatever the rest of the sentence says. The gate passes these
    through and lets the lookup — which is scoped and guarded anyway — have its say.
    """
    if history.strip():
        return True
    if len(_WORD_RE.findall(question or "")) < MIN_WORDS_TO_JUDGE:
        return True
    return bool(_SELF_REF_RE.search(question) or _PERIOD_RE.search(question))


def _names_a_lab_the_caller_cannot_read(question: str, ctx: Ctx) -> bool:
    """The question asks for the figures of a lab this caller is not in.

    The tool layer refuses this too, and so does the scope guard in data.py. Catching it
    here as well means the refusal costs no planner call and no query, and it says the
    same thing either way.
    """
    if ctx.is_admin:
        return False
    if not _LAB_FIGURES_RE.search(question):
        return False
    named = {f"lab-{m.group(1).lower()}" for m in _LAB_REF_RE.finditer(question)}
    return bool(named - {lab.lower() for lab in ctx.lab_ids})


def pre(question: str, ctx: Ctx, history: str = "") -> RelevanceResult:
    """Before anything runs: could a catalogued source answer this, for this caller?

    Guarantees, in order of cost: a question no source covers is refused without a
    planner call; a question whose only sources sit above the caller's role, or which
    asks for another lab's figures, is refused without a query. Anything else passes,
    including everything the gate cannot confidently judge.
    """
    text = f"{history}\n{question}" if history.strip() else question
    considered = sources_for(text)
    subjects = subjects_in(text)

    if not considered:
        if _too_little_to_judge(question, history):
            return RelevanceResult(True, "ok", subjects=subjects)
        return RelevanceResult(False, "no_source_covers_it", subjects=subjects)

    if _names_a_lab_the_caller_cannot_read(question, ctx):
        return RelevanceResult(
            False, "not_entitled", considered=tuple(s.name for s in considered),
            subjects=subjects,
        )

    rank = ROLE_RANK.get(ctx.role, 0)
    entitled = tuple(s.name for s in considered if rank >= ROLE_RANK[s.min_role])
    if not entitled:
        return RelevanceResult(
            False, "not_entitled", considered=tuple(s.name for s in considered),
            subjects=subjects,
        )

    return RelevanceResult(
        True, "ok", considered=tuple(s.name for s in considered),
        entitled=entitled, subjects=subjects,
    )


def _all_null(rows: list[dict]) -> bool:
    """A single aggregate row with nothing in it — an empty SUM, not a figure."""
    return len(rows) == 1 and bool(rows[0]) and all(v is None for v in rows[0].values())


def post(rows: list[dict], scalars: dict[str, Any] | None = None) -> RelevanceResult:
    """After the rows come back: is there anything here to answer with?

    No rows and no facts means no records, and the honest reply to that is to say so and
    say what to check — not to compose a paragraph about an empty table. A lone aggregate
    row of NULLs is the same finding wearing a number's clothes: SUM over nothing is not
    zero, it is nothing, and printing it as a total would state a figure the platform
    never held.
    """
    if _all_null(rows):
        return RelevanceResult(False, "no_values")
    if rows:
        return RelevanceResult(True, "ok")
    if any(value is not None for value in (scalars or {}).values()):
        # A result can be all facts and no rows: an instrument under maintenance has no
        # free windows but a perfectly good reason, and that reason is the answer.
        return RelevanceResult(True, "ok")
    return RelevanceResult(False, "no_records")


REASON_TEXT = {
    "no_source_covers_it": (
        "I have no records that could answer that. I can look up "
        f"{SUBJECTS_IN_ENGLISH} — that question sits outside all of them."
    ),
    "not_entitled": (
        "Answering that would mean reading records beyond what your account covers. I "
        "can show you what is yours, and I will not go wider than that."
    ),
    "no_records": (
        "I found no records matching that. If you expected some, check the period, the "
        "account code, or the name you used — I can only report what the platform has "
        "stored."
    ),
    "no_values": (
        "The platform holds no figures for that, so there is nothing for me to add up. "
        "An empty total is not zero, and I am not going to print it as though it were."
    ),
}

_CLOSING = "For anything further, ask the core facility admin."


def redirect_text(result: RelevanceResult) -> str:
    """The honest redirect for a failed check: what is missing, and where to go instead.

    Plain English only. The reader never asked about a column, so naming one here would
    tell them about our schema instead of about their question.
    """
    return f"{REASON_TEXT.get(result.reason, REASON_TEXT['no_records'])} {_CLOSING}"
