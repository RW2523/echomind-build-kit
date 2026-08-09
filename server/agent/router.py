"""Route a turn to the branch that can answer it honestly.

knowledge | data | action | smalltalk | out_of_scope (spec 04 §1).
"""

from __future__ import annotations

import logging
import re

from server.agent.llm import chat_json

log = logging.getLogger("echomind.router")

ROUTES = ("knowledge", "data", "action", "smalltalk", "out_of_scope")

ROUTE_SCHEMA = {
    "title": "route",
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": list(ROUTES)},
        "why": {"type": "string"},
    },
    "required": ["route", "why"],
    "additionalProperties": False,
}

SYSTEM = """You classify one user message for an assistant that serves the Infinity X
core-facility platform (a shared scientific instrument facility).

Reply only as JSON: {"route": "...", "why": "<10 words"}

Routes:
- "data"        the user wants a number, status, date, list or record from the platform:
                their bookings, usage hours, invoices and charges, request status, sample
                state, instrument availability, project spend. Anything phrased as
                "my/our", "how much", "how many", "when", "what is the status".
- "action"      the user wants to CHANGE something or produce a document: book an
                instrument, submit a service request, onboard a new user, generate a
                report. Requests to do, create, book, submit, register, or generate.
- "knowledge"   the user asks how something works, what a policy or procedure says, or
                what they should do: SOPs, training requirements, cancellation rules,
                billing policy, which instrument to choose.
- "smalltalk"   greetings, thanks, "who are you", "what can you do".
- "out_of_scope" anything unrelated to the facility: general science trivia, other
                software, world knowledge, personal questions, coding help.

The knowledge/data line is about WHERE the answer lives, not whether it is a number:

- A rule, policy, procedure or published rate is "knowledge" — it is written in a
  document and is the same for everybody. It stays "knowledge" even when the answer is
  a number or a date.
- A record belonging to someone or something is "data" — it exists only in the platform
  and differs per user, lab, instrument or month.

Worked examples:
- "When are invoices issued?"                       -> knowledge (billing policy)
- "What is the total on my March invoice?"          -> data (a record)
- "What is the maximum length of a booking?"        -> knowledge (a rule)
- "How long is my Thursday booking?"                -> data (a record)
- "How long is Biosafety Level 2 valid for?"        -> knowledge (a rule)
- "Is my Biosafety training still valid?"           -> data (a record)
- "What does it cost to cancel late?"               -> knowledge (a policy)
- "How much was lab A charged in March?"            -> data (a record)

- "What does my uploaded protocol say about X?"     -> knowledge (a document)
- "What is the marker in my note?"                  -> knowledge (a document)

Other rules:
- "my" and "our" point to "data" only when the subject is a platform RECORD — bookings,
  invoices, usage, requests, samples, training. "My note", "my protocol", "my uploaded
  document" are knowledge: the answer is inside a document, and documents are knowledge
  even when the document belongs to the asker.
- Wanting something to happen is "action", even if phrased as a question
  ("can you book me the confocal on Friday?").
- Classify only the final message. Earlier turns are context for resolving pronouns:
  "book it" after an availability question is "action", not another lookup.
"""

# The router is an LLM call, but obvious cases should not depend on one.
ACTION_HINTS = re.compile(
    r"\b(book|reserve|schedule me|submit|create|onboard|register|generate|draft|"
    r"issue|raise|request)\b",
    re.IGNORECASE,
)
SMALLTALK_RE = re.compile(
    r"^\s*(hi|hey|hello|yo|thanks|thank you|cheers|good morning|good afternoon|"
    r"who are you|what can you do|help)\b[\s!.?]*$",
    re.IGNORECASE,
)


def route(message: str, history: str = "") -> tuple[str, str]:
    """Return (route, why). `history` lets follow-ups like "book it" route correctly."""
    if SMALLTALK_RE.match(message.strip()):
        return "smalltalk", "greeting pattern"

    verdict = chat_json(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{history}\n\nCLASSIFY THIS MESSAGE: {message}".strip()},
        ],
        default={"route": "knowledge", "why": "router unavailable"},
        max_tokens=80,
        schema=ROUTE_SCHEMA,
    )
    chosen = str(verdict.get("route", "")).strip().lower()
    if chosen not in ROUTES:
        log.warning("router returned %r; defaulting to knowledge", chosen)
        chosen = "knowledge"

    why = str(verdict.get("why", ""))[:120]
    log.info("route=%s (%s) for %r", chosen, why, message[:80])
    return chosen, why
