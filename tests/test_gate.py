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


def test_private_content_is_answered_not_refused_on_confidentiality_grounds():
    """Regression: the generator refused to read out a source marked "private/secret".

    Everything in the context has already been permission-filtered for this caller, so
    declining on confidentiality grounds is always wrong — and it silently turned a
    correct answer into a redirect.
    """
    chunks = [
        _chunk(
            "The private upload verification marker is ZEPHYR-5512. It appears in no "
            "other document in this corpus, and only Alice uploaded it.",
            score=0.9,
        )
    ]
    text, citations, sufficient = gen.generate(
        "What is the private upload verification marker?", chunks
    )
    assert sufficient is True
    assert "ZEPHYR-5512" in text
    assert citations


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


def test_applying_a_source_rule_to_the_users_own_value_is_supported():
    """The source gives a threshold; the user asks about a specific value under it.

    Answering "12 hours before start is charged 50%" from "cancellations inside 24 hours
    are charged 50%" is the assistant doing its job, not inventing a number — but without
    the question the judge only sees an unfamiliar "12 hours" and refuses, which turned a
    correct answer into a redirect.
    """
    source = _chunk(
        "Bookings may be cancelled free of charge up to 24 hours before the session "
        "start. Cancellations inside 24 hours are charged at 50% of the booked time."
    )
    verdict = faith.check(
        "If you cancel 12 hours before it starts, you are charged 50% of the booked time [1].",
        [source], [],
        question="What am I charged if I cancel a booking 12 hours before it starts?",
    )
    assert verdict.passed is True


def test_a_value_in_neither_source_nor_question_is_still_refused():
    """The exception above must not become a licence to invent."""
    source = _chunk("Cancellations inside 24 hours are charged at 50% of the booked time.")
    verdict = faith.check(
        "Cancelling inside 24 hours is charged at 80% of the booked time [1].",
        [source], [], question="What am I charged for a late cancellation?",
    )
    assert verdict.passed is False


def test_every_claim_gets_a_verdict_even_with_a_long_source():
    """Regression: the judge used to stop after the first verdict on a long prompt.

    Each claim's source was repeated in full underneath it, tripling the prompt; the 7B
    judge then returned one verdict, and the unjudged claims were failed closed — which
    silently suppressed correct, properly-cited answers.
    """
    long_source = _chunk(
        "Guidance on choosing between instruments. "
        "Confocal C2 - the workhorse point scanner, best for fixed samples. "
        "Spinning Disk SD1 - the right choice for live-cell imaging and anything faster "
        "than roughly one frame per second. Gentler on the sample than a point scanner. "
        "Light Sheet LS7 - cleared whole-mount specimens and large volumes. "
        + ("Additional catalogue guidance text. " * 40)
    )
    answer_text = (
        "For live-cell imaging you should use the Spinning Disk SD1 [1]. It is recommended "
        "for anything faster than roughly one frame per second [1]."
    )
    verdict = faith.check(answer_text, [long_source], [])
    assert verdict.checked == 2
    assert all(
        v.why != "judge returned no verdict" for v in verdict.verdicts
    ), "every claim must get a real verdict, not a fail-closed default"
    assert verdict.passed is True


# --- mis-cited claims are repointed, never rescued ------------------------------------


def test_a_claim_cited_to_the_wrong_source_is_repointed_not_suppressed():
    """Regression (k07): a true, in-context claim was suppressed over a misplaced bracket.

    "Each invoice covers one account code for one calendar month" was cited to the
    onboarding guide, which mentions first invoices in passing, while the Billing FAQ
    that states it verbatim sat in the same context. The judge was right that the cited
    source did not support it — but discarding the whole answer costs the reader a
    correct, fully sourced reply. The citation is repointed instead.
    """
    onboarding = _chunk("Your first invoice arrives in the first week of the "
                        "following month.", idx=1)
    billing = _chunk("Invoices are issued monthly, in arrears, in the first week of the "
                     "following month. Each invoice covers one account code for one "
                     "calendar month.", idx=2)
    verdict = faith.check(
        "Each invoice covers one account code for one calendar month [1].",
        [onboarding, billing], [],
        question="When are invoices issued?",
    )
    assert verdict.passed is True
    assert verdict.corrections, "the claim should have been traced to the other source"
    claim, corrected_to = verdict.corrections[0]
    assert corrected_to == 2, "it is source [2] that states this"


def test_repair_cannot_rescue_a_claim_no_source_states():
    """The repair pass must widen where we look, never lower the bar for what counts."""
    onboarding = _chunk("Your first invoice arrives in the first week of the "
                        "following month.", idx=1)
    billing = _chunk("Invoices are issued monthly, in arrears.", idx=2)
    verdict = faith.check(
        "Invoices are issued every fortnight and carry a 12% surcharge [1].",
        [onboarding, billing], [],
        question="When are invoices issued?",
    )
    assert verdict.passed is False
    assert verdict.unsupported
    assert not verdict.corrections


def test_repointing_rewrites_only_the_sentence_it_names():
    corrected = gen.apply_citation_corrections(
        "First claim [1]. Second claim [1]. Third claim [2].",
        [("Second claim", 3)],
    )
    assert corrected == "First claim [1]. Second claim [3]. Third claim [2]."


def test_repointing_leaves_a_sentence_it_cannot_find_untouched():
    """A correction that does not match must not rewrite some other sentence."""
    original = "The warm-up is 30 minutes [1]. Bookings need an account code [2]."
    assert gen.apply_citation_corrections(original, [("Nothing like this", 3)]) == original


def test_repointing_is_a_no_op_without_corrections():
    original = "The warm-up is 30 minutes [1]."
    assert gen.apply_citation_corrections(original, []) == original


# --- sourcing remarks are not claims --------------------------------------------------


def test_a_sentence_that_only_says_where_the_answer_came_from_is_dropped():
    """Regression: "This is specified in source [1]." asserts nothing about the facility,
    but the claim splitter saw a cited sentence and asked the judge to verify it. No
    source can state it, so a correct answer was downgraded over a sentence carrying no
    information. Rule 5 of the generation prompt already forbids these.
    """
    cleaned = gen.strip_meta_sentences(
        "Sample barcodes use `BC` followed by six digits [1]. This is specified in source [1]."
    )
    assert cleaned == "Sample barcodes use `BC` followed by six digits [1]."


def test_stripping_keeps_a_sentence_that_merely_begins_with_this():
    original = "This protocol is described in the Confocal C2 SOP and takes 30 minutes [1]."
    assert gen.strip_meta_sentences(original) == original


def test_stripping_keeps_a_factual_sentence_containing_the_word_source():
    original = "The source of the sample must be recorded on submission [1]."
    assert gen.strip_meta_sentences(original) == original


def test_stripping_keeps_a_sentence_that_attributes_and_then_asserts():
    """"According to source [2], X" carries X — it is not a bare sourcing remark."""
    original = "According to source [2], cancellations inside 24 hours are charged 50%."
    assert gen.strip_meta_sentences(original) == original


def test_an_answer_that_is_only_a_sourcing_remark_is_left_alone():
    """Better to let the faithfulness check reject it than to hand back a blank reply."""
    original = "This is specified in source [1]."
    assert gen.strip_meta_sentences(original) == original


def test_dropping_a_sourcing_remark_carries_its_citation_back():
    """Regression: the remark held the answer's only citation, so removing the noise left
    an uncited answer — which generation treats as insufficient. A correct answer became
    a redirect. The attribution is real and belongs on the sentence it describes."""
    cleaned = gen.strip_meta_sentences(
        "Sample barcodes use `BC` followed by six digits. This is specified in source [1]."
    )
    assert cleaned == "Sample barcodes use `BC` followed by six digits [1]."
    assert gen.cited_indices(cleaned, limit=4) == [1]


def test_carry_back_does_not_overwrite_an_existing_citation():
    cleaned = gen.strip_meta_sentences(
        "Barcodes are `BC` plus six digits [2]. This is specified in source [1]."
    )
    assert cleaned == "Barcodes are `BC` plus six digits [2]."
