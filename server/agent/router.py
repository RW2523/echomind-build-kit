"""Route a turn to the branch that can answer it honestly.

knowledge | data | action | smalltalk | out_of_scope (spec 04 §1).
"""

from __future__ import annotations

import logging
import re

from server.agent.llm import chat_json
from server.agent.prompts import register

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
                state, instrument availability, project spend, and their own profile —
                account codes, training records, role, lab membership. Anything phrased
                as "my/our", "how much", "how many", "when", "what is the status".
                ALSO the facility directory: where a core is, its campus, building, room,
                address, opening hours, how far away it is, and what an instrument can do
                — its techniques, modality and sample types. Those are recorded fields on
                the facility and instrument, so "which core does cryo-EM", "where is the
                nearest one", and "what should I use for live-cell imaging" are all data.
- "action"      the user wants to CHANGE something or produce a document: book an
                instrument, submit a service request, onboard a new user, generate a
                report. Requests to do, create, book, submit, register, or generate.
- "knowledge"   the user asks how something works, what a policy or procedure says, or
                what they should do: SOPs, training requirements, cancellation rules,
                billing policy, how to prepare a sample.
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
- "How are account codes assigned?"                 -> knowledge (a policy)
- "Where do I park at the imaging core?"            -> knowledge (nothing records parking)
- "What account codes can I charge to?"             -> data (mine, on my profile)
- "Who is in my project?"                           -> data (a record)
- "Where is the nearest core that does cryo-EM?"    -> data (facility location)
- "Which instrument should I use for RNA-seq?"      -> data (instrument capability)
- "I want to image live cells, what can I use?"     -> data (instrument capability)
- "What are the imaging core's opening hours?"      -> data (a facility record)
- "How do I prepare a sample for cryo-EM?"          -> knowledge (a procedure)

- "What does my uploaded protocol say about X?"     -> knowledge (a document)
- "What is the marker in my note?"                  -> knowledge (a document)

Other rules:
- "my" and "our" point to "data" only when the subject is a platform RECORD — bookings,
  invoices, usage, requests, samples, training. "My note", "my protocol", "my uploaded
  document" are knowledge: the answer is inside a document, and documents are knowledge
  even when the document belongs to the asker.
- A question about the facility itself splits on whether the platform RECORDS the answer.
  Recorded fields — where a core is, its campus, building, room, address, opening hours,
  contact, and what each instrument can do — are "data". Everything else about the
  premises — parking, what to wear, how to get in, safety practice — is "knowledge", even
  when no document covers it. It is never "out_of_scope": that is for subjects the
  facility has nothing to do with. Getting this wrong costs more than it looks — an
  uncovered facility question becomes an honest redirect that records a gap for someone to
  write up, while "out of scope" tells the user they asked the wrong assistant and records
  nothing.
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

# Versioned by content hash — see server/agent/prompts.py.
VERSION_SYSTEM = register("router.system", SYSTEM)
