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
#
# This one is not obvious to a classifier and is obvious to a person: "give me the March
# invoice" asks for the document, and the router sent it to the data branch, which
# answered with the invoice's figures instead. Truthful, and not what was asked — and
# inconsistent besides, since "give me an invoice" followed by "March 2026" produces the
# document. The same intent reaching two destinations is the defect.
#
# Deliberately narrow. It needs a delivery verb AND a noun that names an artefact this
# system can actually render. "summary" is excluded on purpose: "give me the billing
# summary" is get_billing_summary's job, and capturing it here would trade this bug for a
# worse one. So is "bookings" — a list of them is data, not a document.
_DELIVERY_VERB = (
    r"(?:give|send|get|email|mail)\s+(?:me|us|it)\b"
    r"|(?:i|we)\s+(?:want|need|would like)\b"
    r"|\b(?:produce|prepare|export|download)\b"
)
_ARTEFACT_NOUN = (
    r"\binvoice|\bstatement|\bdirectory|\bconfirmation|\bpacket"
    r"|\bcapability report|\bas a (?:pdf|docx|document|word)|\bin (?:pdf|docx)"
)
DOCUMENT_REQUEST_RE = re.compile(
    rf"(?=.*(?:{_DELIVERY_VERB}))(?=.*(?:{_ARTEFACT_NOUN}))", re.IGNORECASE | re.DOTALL
)

# A question about the asker's own record is a lookup, not a policy question.
#
# "Am I trained on the confocal?" routed to knowledge on its own and to data once the
# conversation had context — a turn balanced on the boundary, which is another way of
# saying it answers differently depending on nothing the user did. The knowledge branch
# would answer it from the training *policy*, which describes what training requires and
# cannot say whether this person has it.
#
# Narrow on purpose: first person, and a noun naming something the platform holds about
# them. "What is the training policy" has no first person and stays where it belongs.
_SELF_RECORD_RE = re.compile(
    r"\b(?:am|are|do|does|have|has|can|was|were)\s+i\b.*"
    r"\b(?:trained|training|certifi\w*|approved|authoris\w*|authoriz\w*|booked|"
    r"bookings?|registered|enrolled|charged|billed|allowed|invoices?|usage|"
    r"samples?)\b"
    r"|\bmy\s+(?:training|certification|bookings?|usage|invoices?|charges|samples?|"
    r"requests?|account codes?|profile|record)\b",
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
    if DOCUMENT_REQUEST_RE.search(message):
        return "action", "asks for a document by name"
    if _SELF_RECORD_RE.search(message):
        return "data", "asks about the caller's own record"

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
