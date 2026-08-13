"""Applying the facility's own rules, and being able to show which one was applied.

The prose lives in the knowledge corpus and is what a person should read. `policy.statements`
is the same rules in the form a program can apply — a cancellation window as a number of
hours, a late charge as a percentage — each row carrying the document and clause it came
from.

Why this exists as data at all: "you can cancel free of charge" is a claim about a rule,
and a model paraphrasing a paragraph is exactly where a confident wrong answer comes from.
Here the decision is arithmetic over a row, and the sentence shown to the user is the
sentence stored in that row. The system never composes the rule; it quotes it.

Nothing in this module invents a figure. If the applicable statement carries no
`charge_percent`, the outcome says the charge is not stated rather than assuming zero —
an unstated charge is unknown, not free, and guessing in the user's favour is still
guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from server.db import session_scope

log = logging.getLogger("echomind.policy")


@dataclass(frozen=True)
class Statement:
    """One rule, as stored, with the clause it came from."""

    id: str
    domain: str
    subject: str
    title: str
    statement: str
    threshold_hours: Decimal | None
    charge_percent: Decimal | None
    source_doc_id: str | None
    source_clause: str | None
    version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "subject": self.subject,
            "title": self.title,
            "statement": self.statement,
            "threshold_hours": _number(self.threshold_hours),
            "charge_percent": _number(self.charge_percent),
            "source_doc_id": self.source_doc_id,
            "source_clause": self.source_clause,
            "version": self.version,
        }


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


_SELECT = """
SELECT id, domain, subject, title, statement, threshold_hours, charge_percent,
       source_doc_id, source_clause, version
FROM policy.statements
-- Cast explicitly: Postgres cannot infer a type for a bare parameter compared only to
-- NULL, and the same pattern is used by the booking tools for date_from/date_to.
WHERE (CAST(:domain AS TEXT) IS NULL OR domain = :domain)
  AND (CAST(:subject AS TEXT) IS NULL OR subject = :subject)
  AND effective_from <= CURRENT_DATE
ORDER BY domain, subject, COALESCE(threshold_hours, 0) DESC, id
"""


def statements(domain: str | None = None, subject: str | None = None) -> list[Statement]:
    """The rules in force today, most permissive threshold first.

    `effective_from <= CURRENT_DATE` rather than every row: a rule that has been written
    down but does not apply yet must not be quoted at someone as though it did.
    """
    with session_scope() as s:
        rows = s.execute(text(_SELECT), {"domain": domain, "subject": subject}).mappings().all()
    return [Statement(**dict(row)) for row in rows]


@dataclass(frozen=True)
class Outcome:
    """What the rules say about one proposed change, and why.

    `charge_percent` is None when the applicable statement does not state one. The caller
    must render that as "not stated" — never as zero.
    """

    decision: str                       # free | charged | charge_not_stated | too_late
    charge_percent: float | None
    charged_hours: float | None
    hours_notice: float
    applied: Statement | None
    considered: tuple[Statement, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "charge_percent": self.charge_percent,
            "charged_hours": self.charged_hours,
            "hours_notice": round(self.hours_notice, 2),
            "applied": self.applied.to_dict() if self.applied else None,
            "policy": [s.to_dict() for s in self.considered],
        }


def cancellation_outcome(
    starts_at: datetime, ends_at: datetime, now: datetime | None = None
) -> Outcome:
    """What cancelling this booking costs, by the rule that covers the notice given.

    The rules are a ladder of thresholds: cancel with more notice than the largest
    threshold and nothing is charged; inside it, the narrowest threshold the notice falls
    within is the one that applies. Reading them in stored order and taking the first
    match would make the outcome depend on primary keys, so they are sorted by threshold
    and the tightest applicable one wins.

    Notice can be negative — a booking that has already started. The rules as written cover
    cancellation before the start and no-shows; neither is "cancelling something already
    under way", so that returns `too_late` rather than picking the nearest-looking clause.
    """
    moment = now or datetime.now(UTC)
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=UTC)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=UTC)

    hours_notice = (starts_at - moment).total_seconds() / 3600
    booked_hours = (ends_at - starts_at).total_seconds() / 3600
    rules = tuple(statements(domain="booking", subject="cancellation"))

    if hours_notice < 0:
        return Outcome("too_late", None, None, hours_notice, None, rules)

    # Only the rules that actually carry a window can be compared against the notice.
    with_threshold = [r for r in rules if r.threshold_hours is not None]
    if not with_threshold:
        log.warning("no cancellation rule carries a threshold; cannot decide a charge")
        return Outcome("charge_not_stated", None, None, hours_notice, None, rules)

    widest = max(float(r.threshold_hours) for r in with_threshold)
    if hours_notice > widest:
        # More notice than any rule asks for. The free-of-charge rule is the one that says
        # so, and it is quoted rather than inferred.
        free = next(
            (r for r in with_threshold
             if r.charge_percent is not None and float(r.charge_percent) == 0),
            None,
        )
        if free is None:
            return Outcome("charge_not_stated", None, None, hours_notice, None, rules)
        return Outcome("free", 0.0, 0.0, hours_notice, free, rules)

    # Inside the window: the tightest rule whose threshold still covers this notice.
    applicable = sorted(
        (r for r in with_threshold if hours_notice <= float(r.threshold_hours)),
        key=lambda r: float(r.threshold_hours),
    )
    # A charging rule is the point of this branch; a same-threshold rule that states no
    # charge (the free one shares the 24-hour figure) would otherwise win on sort order.
    charging = next((r for r in applicable if r.charge_percent), None)
    applied = charging or (applicable[0] if applicable else None)
    if applied is None or applied.charge_percent is None:
        return Outcome("charge_not_stated", None, None, hours_notice, applied, rules)

    percent = float(applied.charge_percent)
    return Outcome(
        "charged" if percent else "free",
        percent,
        round(booked_hours * percent / 100, 2),
        hours_notice,
        applied,
        rules,
    )


def reschedule_statements() -> list[Statement]:
    """What the rules say about moving a booking.

    Moving is defined as a cancellation plus a new booking, so a reschedule has to show
    the cancellation consequence too — the user is agreeing to both halves.
    """
    return statements(domain="booking", subject="reschedule")


def describe(outcome: Outcome) -> str:
    """One sentence a person can act on, built only from stored values.

    The rule's own wording is quoted rather than restated. Everything around it is
    arithmetic on numbers that came from the row.
    """
    if outcome.decision == "too_late":
        return (
            "That booking has already started, and the cancellation rules cover notice "
            "given before the start. The core facility admin has to handle this one."
        )
    if outcome.applied is None or outcome.charge_percent is None:
        return (
            "I can see the booking, but the rules on file do not state a charge for "
            "cancelling with this much notice. The core facility admin can confirm it."
        )

    notice = f"{outcome.hours_notice:.1f} hours"
    if outcome.charge_percent == 0:
        return (
            f"Cancelling now gives {notice} of notice, so there is no charge. "
            f"{outcome.applied.statement}"
        )
    return (
        f"Cancelling now gives {notice} of notice, so {outcome.charge_percent:g}% of the "
        f"booked time is charged — {outcome.charged_hours:g} of "
        f"{outcome.charged_hours / (outcome.charge_percent / 100):g} hours. "
        f"{outcome.applied.statement}"
    )
