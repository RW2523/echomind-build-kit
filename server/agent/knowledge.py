"""The knowledge branch: retrieve -> gate -> generate -> faithfulness.

Three independent chances to stop, and only one way to produce an answer: pass all of
them. Anything else is an honest redirect.
"""

from __future__ import annotations

import logging

from server.agent import faithfulness as faith
from server.agent import gaps
from server.agent import gate as gate_mod
from server.agent import generate as gen
from server.agent.gate import GateResult
from server.agent.responses import AgentResponse
from server.auth import Ctx
from server.rag.retrieval import retrieve

log = logging.getLogger("echomind.knowledge")


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


def answer(question: str, ctx: Ctx, k: int = 8) -> AgentResponse:
    chunks = retrieve(question, ctx, k=k)
    gate = gate_mod.evaluate(question, chunks)

    if not gate.passed:
        log.info("gate blocked answer: %s (top_score=%.3f)", gate.reason, gate.top_score)
        return _redirect(question, gate, ctx=ctx)

    text, citations, sufficient = gen.generate(question, chunks)
    if not sufficient:
        # The generator saw the same sources the gate approved and still declined, or
        # produced nothing citable. Either way we do not ship it.
        gate.passed = False
        gate.reason = "no_coverage"
        return _redirect(question, gate, {"declined_at": "generation"}, ctx=ctx)

    verdict = faith.check(text, chunks, citations, question=question)

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
