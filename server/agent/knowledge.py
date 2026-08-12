"""The knowledge branch: retrieve -> gate -> generate -> faithfulness.

Three independent chances to stop, and only one way to produce an answer: pass all of
them. Anything else is an honest redirect.
"""

from __future__ import annotations

import logging
import re

from server.agent import faithfulness as faith
from server.agent import gaps, progress, rewrite
from server.agent import gate as gate_mod
from server.agent import generate as gen
from server.agent.gate import GateResult
from server.agent.responses import AgentResponse, clarify_response
from server.auth import Ctx
from server.rag.retrieval import retrieve

log = logging.getLogger("echomind.knowledge")

# A question about the booking the user is in the middle of preparing — "which instrument
# am I about to book?" — is about conversation state, not the corpus. Routed to knowledge
# it retrieved a tangential SOP and answered, confidently, "Cryo-EM Titan" — an instrument
# never mentioned, one turn after the user set it to Confocal C2. The corpus cannot answer
# a question about the pending action; the conversation can. Deliberately narrow: it must
# say "book", so "which instrument am I trained on?" is untouched.
_PENDING_BOOKING_Q = re.compile(
    r"\b(about to book|going to book"
    r"|which (?:instrument|one) am i (?:about to |going to )?book"
    r"|what am i (?:about to |going to )?book"
    r"|what did i just book"
    r"|my (?:pending|prepared|current) booking"
    r"|what(?:'s| is) my (?:pending|prepared|current) booking)\b",
    re.IGNORECASE,
)
_PROPOSED_BOOKING_RE = re.compile(r"Book\s+(.+?)\s+for\s+[\d.]+\s*h\b", re.IGNORECASE)


def _pending_booking_answer(question: str, history: str) -> AgentResponse | None:
    """Answer a question about the in-progress booking from state, not from the corpus."""
    if not _PENDING_BOOKING_Q.search(question):
        return None
    proposals = _PROPOSED_BOOKING_RE.findall(history or "")
    if proposals:
        instrument = proposals[-1].strip()
        log.info("answering a pending-booking question from state: %s", instrument)
        return AgentResponse(
            response_type="answer",
            route="knowledge",
            text=(
                f"You're about to book {instrument} — it is prepared and waiting for your "
                "approval. Nothing has been booked yet; approve it to go ahead, or decline."
            ),
        )
    return AgentResponse(
        response_type="redirect",
        route="knowledge",
        text=(
            "You don't have a booking prepared right now. Ask me to book an instrument and "
            "I'll prepare it for your approval first."
        ),
    )


def _redirect(question: str, gate: GateResult, extra: dict | None = None,
              ctx: Ctx | None = None) -> AgentResponse:
    # Every refusal is a document somebody still has to write, so it is recorded before
    # it is returned. Best effort by design — see server/agent/gaps.py.
    if ctx is not None:
        gaps.record(
            question, user_id=ctx.user_id, role=ctx.role,
            reason=gate.reason or "unknown",
            top_score=gate.top_score, closest_doc=gate.closest_breadcrumb,
        )
    return AgentResponse(
        response_type="redirect",
        text=gate_mod.redirect_text(question, gate),
        gate=gate.to_dict(),
        route="knowledge",
        meta=extra or {},
    )


def answer(question: str, ctx: Ctx, k: int = 8, history: str = "") -> AgentResponse:
    # A question about the booking in progress is answered from the conversation, not the
    # corpus — the corpus has no idea what the caller is about to book, and answering from
    # it invents an instrument.
    if (pending := _pending_booking_answer(question, history)) is not None:
        return pending

    # Retrieval and the judges work on the resolved question; the user's own words are
    # what they see. "How long is that?" retrieves on five stopwords otherwise.
    if rewrite.is_unresolvable(question, history):
        # Nothing to resolve the reference against. Answering anyway would produce a
        # fluent, cited reply about whatever ranked first, which is worse than asking.
        log.info("unresolved reference with no history: %r", question[:60])
        return clarify_response(question)

    resolved = rewrite.standalone(question, history)
    chunks = retrieve(resolved, ctx, k=k)
    progress.emit(f"searched the documents you can see — {len(chunks)} passage(s) matched")
    gate = gate_mod.evaluate(resolved, chunks)
    progress.emit("checked confidence" if gate.passed else "not confident enough to answer")

    if not gate.passed:
        log.info("gate blocked answer: %s (top_score=%.3f)", gate.reason, gate.top_score)
        return _redirect(question, gate, ctx=ctx)

    text, citations, sufficient = gen.generate(resolved, chunks)
    if not sufficient:
        # The generator saw the same sources the gate approved and still declined, or
        # produced nothing citable. Either way we do not ship it.
        gate.passed = False
        gate.reason = "no_coverage"
        return _redirect(question, gate, {"declined_at": "generation"}, ctx=ctx)

    progress.emit("drafted an answer from those passages")
    verdict = faith.check(text, chunks, citations, question=resolved)
    progress.emit("checked every claim against its source"
                  if verdict.passed else "a claim could not be verified")

    # Claims the judge traced to a source other than the one cited: repoint the marker and
    # rebuild the citation list, so what the reader clicks is the document that actually
    # states the sentence. Nothing here can rescue an unsupported claim — only a claim the
    # judge found, verbatim, in another chunk this caller was already permitted to see.
    if verdict.corrections:
        log.info("repointing %d mis-cited claim(s)", len(verdict.corrections))
        text = gen.apply_citation_corrections(text, verdict.corrections)
        citations = gen.build_citations(chunks, gen.cited_indices(text, len(chunks)))

    if not verdict.passed:
        log.info("faithfulness downgraded answer: %s", verdict.unsupported)
        gate.passed = False
        gate.reason = "unfaithful"
        response = _redirect(question, gate, {"declined_at": "faithfulness"}, ctx=ctx)
        response.faithfulness = verdict.to_dict()
        return response

    return AgentResponse(
        response_type="answer",
        text=text,
        citations=citations,
        gate=gate.to_dict(),
        faithfulness=verdict.to_dict(),
        route="knowledge",
    )
