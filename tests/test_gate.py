"""M4 verification — confidence gate, grounded generation, faithfulness (pytest -m gate).

The three LLM-backed paths are exercised once each at module scope and then asserted
against, so the suite stays quick while still testing the real model.
"""

from __future__ import annotations

import dataclasses

import pytest

from server.agent import faithfulness as faith
from server.agent import gate as gate_mod
from server.agent import generate as gen
from server.agent.gate import GateResult
from server.agent.knowledge import answer
from server.config import settings
from server.rag.retrieval import RetrievedChunk, retrieve

pytestmark = pytest.mark.gate

IN_CORPUS = "How long do the confocal lasers need to warm up before quantitative imaging?"
OUT_OF_CORPUS = "What is the facility's parking permit policy for visiting researchers?"


def _chunk(text: str, score: float = 0.9, idx: int = 1) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=idx, doc_id=f"doc-{idx}", text=text, breadcrumb=f"Doc {idx} (v1)",
        score=score, rrf=0.5, vector_rank=idx, fts_rank=idx, title=f"Doc {idx}",
        visibility="public",
    )


# --- pure gate logic (no model) -----------------------------------------------------


def test_gate_fails_closed_with_no_permitted_sources():
    result = gate_mod.evaluate("anything", [])
    assert result.passed is False
    assert result.reason == "no_permitted_sources"
    assert result.top_score == 0.0


def test_gate_rejects_below_the_score_floor():
    low = settings.gate_min_top_score - 0.1
    result = gate_mod.evaluate("anything", [_chunk("unrelated text", score=low)])
    assert result.passed is False
    assert result.reason == "below_score_floor"


def test_gate_thresholds_come_from_env():
    result = gate_mod.evaluate("anything", [])
    assert result.thresholds["min_top_score"] == settings.gate_min_top_score
    assert result.thresholds["reranker"] == settings.reranker


def test_redirect_text_names_the_closest_document_and_a_person():
    gate = GateResult(passed=False, reason="no_coverage", top_score=0.5,
                      closest_breadcrumb="Billing FAQ > Rates (v2.0)")
    text = gate_mod.redirect_text("how much am I charged per hour?", gate)
    assert "Billing FAQ > Rates (v2.0)" in text
    assert "core facility admin" in text


def test_redirect_points_lab_protocol_questions_at_the_pi():
    gate = GateResult(passed=False, reason="below_score_floor", top_score=0.2,
                      closest_breadcrumb="Some Doc (v1)")
    text = gate_mod.redirect_text("what antibody dilution does our house protocol use?", gate)
    assert "your PI" in text


def test_redirect_never_contains_a_guess():
    gate = GateResult(passed=False, reason="no_coverage", top_score=0.5,
                      closest_breadcrumb="Doc (v1)")
    text = gate_mod.redirect_text("how long is the warm-up?", gate)
    assert "30 minutes" not in text
    assert "minutes" not in text.replace("I would rather", "")


# --- citation plumbing (no model) ---------------------------------------------------


def test_out_of_range_citations_are_discarded():
    assert gen.cited_indices("A claim [1] and another [9].", limit=2) == [1]


def test_invalid_citation_markers_are_stripped_from_the_text():
    cleaned = gen.strip_invalid_citations("Real [1]. Invented [42].", limit=2)
    assert "[1]" in cleaned
    assert "[42]" not in cleaned


def test_claim_splitter_finds_factual_sentences():
    claims = faith.split_claims(
        "The warm-up is 30 minutes [1]. Let me know if you need more detail."
    )
    assert len(claims) == 1
    assert claims[0][1] == [1]


def test_claim_splitter_keeps_uncited_factual_claims():
    """An uncited number must still be checked, not skipped."""
    claims = faith.split_claims("The warm-up is 45 minutes.")
    assert len(claims) == 1
    assert claims[0][1] == []


# --- the real path, end to end ------------------------------------------------------


@pytest.fixture(scope="module")
def in_corpus_answer(ctxs):
    return answer(IN_CORPUS, ctxs["alice"])


@pytest.fixture(scope="module")
def out_of_corpus_answer(ctxs):
    return answer(OUT_OF_CORPUS, ctxs["alice"])


def test_in_corpus_question_yields_a_cited_answer(in_corpus_answer):
    r = in_corpus_answer
    assert r.response_type == "answer"
    assert r.citations, "an answer must carry citations"
    assert r.gate["passed"] is True
    assert r.faithfulness["passed"] is True


def test_in_corpus_answer_states_the_verified_fact(in_corpus_answer):
    assert "30" in in_corpus_answer.text


def test_every_citation_points_at_a_real_retrieved_chunk(in_corpus_answer, ctxs):
    permitted = {c.chunk_id for c in retrieve(IN_CORPUS, ctxs["alice"], k=8)}
    for citation in in_corpus_answer.citations:
        assert citation.chunk_id in permitted
        assert citation.breadcrumb


def test_out_of_corpus_question_yields_a_redirect_not_a_guess(out_of_corpus_answer):
    r = out_of_corpus_answer
    assert r.response_type == "redirect"
    assert r.gate["passed"] is False
    assert not r.citations
    assert "parking" not in r.text.lower(), "the redirect must not invent a parking policy"


def test_redirect_explains_itself_and_offers_a_next_step(out_of_corpus_answer):
    text = out_of_corpus_answer.text
    assert "ask" in text.lower()
    assert len(text) > 80


# --- faithfulness downgrades a tampered context --------------------------------------


def test_tampered_context_downgrades_a_previously_good_answer(in_corpus_answer, ctxs):
    """Same answer text, sources swapped for unrelated ones: it must no longer pass."""
    good = in_corpus_answer
    assert good.response_type == "answer"

    real_chunks = retrieve(IN_CORPUS, ctxs["alice"], k=8)
    tampered = [
        dataclasses.replace(
            c,
            text=(
                "The facility cafeteria serves lunch between 12:00 and 14:00. "
                "Vending machines are located on the ground floor."
            ),
        )
        for c in real_chunks
    ]

    verdict = faith.check(good.text, tampered, good.citations)
    assert verdict.passed is False
    assert verdict.unsupported
    assert verdict.score < 1.0


def test_faithfulness_rejects_an_answer_with_no_checkable_claim():
    verdict = faith.check("Sure thing.", [_chunk("some source")], [])
    assert verdict.passed is False
    assert verdict.checked == 0


def test_faithfulness_accepts_a_claim_the_source_states():
    chunks = [_chunk("Lasers must warm up for 30 minutes before quantitative imaging.")]
    verdict = faith.check("The lasers warm up for 30 minutes [1].", chunks, [])
    assert verdict.passed is True
    assert verdict.score == 1.0


def test_faithfulness_rejects_a_number_the_source_does_not_contain():
    chunks = [_chunk("Lasers must warm up for 30 minutes before quantitative imaging.")]
    verdict = faith.check("The lasers warm up for 90 minutes [1].", chunks, [])
    assert verdict.passed is False
