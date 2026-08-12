"""Discovery tools — find_facilities (16) and recommend_instrument (17).

"Where is the nearest core that does cryo-EM?" and "which instrument for single-cell
RNA-seq?" are capability questions, and the two ways to get them wrong are both worse
than silence: naming a facility that cannot do the work, or hiding one that can because
its instrument happens to be in maintenance. Both tools are pure arithmetic and string
matching against seeded rows, so both are asserted exactly rather than approximately.
"""

from __future__ import annotations

import pytest

from server.mcp import tools as T
from server.mcp.errors import ToolError

pytestmark = pytest.mark.tools

# Seeded coordinates (migration 008). Repeated here on purpose: a test that reads its
# expected values out of the same query it is checking asserts nothing.
IMAGING = (51.524310, -0.133910)
GENOMICS = (51.526870, -0.129440)
MASSSPEC = (51.498220, -0.176500)

CARD_TONES = {"ok", "warn", "info", "muted"}


def _assert_card_contract(card: dict, kind: str) -> None:
    """Every field of the shared card contract, checked structurally.

    The UI renders this instead of a table, so a card that is subtly the wrong shape is a
    blank panel in front of a user rather than a stack trace in front of us.
    """
    assert card["kind"] == kind
    assert isinstance(card["title"], str) and card["title"]
    assert card["subtitle"] is None or isinstance(card["subtitle"], str)
    assert card["footer"] is None or isinstance(card["footer"], str)
    for f in card["fields"]:
        assert set(f) == {"label", "value", "emphasis"}
        assert isinstance(f["label"], str) and isinstance(f["value"], str)
        assert isinstance(f["emphasis"], bool)
    for item in card["items"]:
        assert set(item) == {"title", "subtitle", "meta", "badges", "value"}
        assert isinstance(item["title"], str)
        assert item["subtitle"] is None or isinstance(item["subtitle"], str)
        assert all(isinstance(m, str) for m in item["meta"])
        assert item["value"] is None or isinstance(item["value"], str)
        for badge in item["badges"]:
            assert set(badge) == {"text", "tone"}
            assert isinstance(badge["text"], str)
            assert badge["tone"] in CARD_TONES


# --- the haversine ------------------------------------------------------------------


def test_the_haversine_matches_a_known_distance():
    """London to Paris is 343 km by great circle — the standard worked example.

    Asserted against an independent figure rather than against itself, because the whole
    point of the formula here is that the number reaches a user as "4.1 km away".
    """
    km = T._haversine_km(51.5007, -0.1246, 48.8567, 2.3508)
    assert abs(km - 343.5) < 1.0


def test_the_haversine_is_zero_for_a_point_against_itself():
    assert T._haversine_km(*IMAGING, *IMAGING) == 0.0


def test_the_haversine_is_symmetric():
    there = T._haversine_km(*IMAGING, *MASSSPEC)
    back = T._haversine_km(*MASSSPEC, *IMAGING)
    assert round(there, 6) == round(back, 6)


def test_two_buildings_on_one_campus_are_a_few_hundred_metres_apart():
    """A campus-scale sanity check: the imaging and genomics cores are a walk apart.

    A formula that silently swapped latitude and longitude, or used degrees where it
    wanted radians, still returns a plausible-looking number for one pair. It does not
    return a plausible number at both scales.
    """
    km = T._haversine_km(*IMAGING, *GENOMICS)
    assert 0.35 < km < 0.5


# --- 16. find_facilities ------------------------------------------------------------


def test_facilities_are_returned_nearest_first(ctxs):
    out = T.find_facilities(ctxs["bob"], near_latitude=MASSSPEC[0], near_longitude=MASSSPEC[1])
    distances = [f["distance_km"] for f in out["facilities"]]
    assert distances == sorted(distances), "nearest first is the whole point of the location"
    assert out["facilities"][0]["id"] == "fac-massspec"
    assert distances[0] == 0.0
    assert all(round(d, 2) == d for d in distances), "distance_km is rounded to 2dp"


def test_the_nearest_core_that_does_cryo_em_is_the_imaging_core(ctxs):
    """The question this tool exists for, end to end."""
    out = T.find_facilities(
        ctxs["bob"], technique="cryo-EM",
        near_latitude=GENOMICS[0], near_longitude=GENOMICS[1],
    )
    assert [f["id"] for f in out["facilities"]] == ["fac-imaging"]
    facility = out["facilities"][0]
    assert facility["building"] == "Wellcome Building"
    assert facility["contact_email"] == "imaging-core@example.edu"
    assert facility["opening_hours"]
    assert 0.35 < facility["distance_km"] < 0.5
    assert [i["id"] for i in facility["instruments"]] == ["ins-em-titan"]


def test_a_technique_returns_only_the_instruments_that_do_it(ctxs):
    """A facility is listed with the instruments that match, not with all of its kit."""
    out = T.find_facilities(ctxs["bob"], technique="confocal microscopy")
    assert out["matched"] == 1
    names = [i["name"] for i in out["facilities"][0]["instruments"]]
    assert names == ["Confocal C2", "Confocal C3"]
    assert "Cryo-EM Titan" not in names, "the imaging core's other kit is not an answer"


def test_a_technique_nothing_does_returns_nothing_rather_than_everything(ctxs):
    """The refusal that matters: an unmatched search must not fall back to the directory.

    Returning all three cores under the heading "facilities that do teleportation" is a
    fabricated capability claim wearing the clothes of a search result, and the reader has
    no way to tell it apart from a real one.
    """
    out = T.find_facilities(ctxs["bob"], technique="teleportation")
    assert out["matched"] == 0
    assert out["facilities"] == []
    assert out["matched_instruments"] == 0
    assert out["card"]["items"] == []
    assert "teleportation" in out["card"]["footer"]


def test_technique_matching_ignores_case_and_punctuation(ctxs):
    """'CRYO EM', 'cryo-em' and 'Cryo-EM' are one question spelled three ways."""
    found = [
        T.find_facilities(ctxs["bob"], technique=spelling)["facilities"][0]["instruments"][0]["id"]
        for spelling in ("cryo-EM", "CRYO EM", "cryo em")
    ]
    assert found == ["ins-em-titan"] * 3


def test_a_technique_containing_a_sql_wildcard_matches_nothing(ctxs):
    """A '%' a user typed is a character, not a wildcard.

    Matched in Python rather than with ILIKE precisely so that this question cannot
    quietly become "show me everything".
    """
    out = T.find_facilities(ctxs["bob"], technique="%")
    assert out["matched"] == 0


def test_a_blank_technique_is_the_absence_of_a_filter(ctxs):
    """A planner spells "no technique given" as both None and "", and means the same."""
    assert T.find_facilities(ctxs["bob"], technique="")["matched"] == 3
    assert T.find_facilities(ctxs["bob"], technique="   ")["matched"] == 3


def test_a_modality_is_searchable_as_well_as_a_technique(ctxs):
    out = T.find_facilities(ctxs["bob"], technique="mass spectrometry")
    assert [f["id"] for f in out["facilities"]] == ["fac-massspec"]
    assert len(out["facilities"][0]["instruments"]) == 3


def test_a_campus_filter_narrows_the_directory(ctxs):
    out = T.find_facilities(ctxs["bob"], campus="Riverside")
    assert [f["id"] for f in out["facilities"]] == ["fac-massspec"]
    assert T.find_facilities(ctxs["bob"], campus="north")["matched"] == 2


def test_the_whole_directory_is_returned_when_nothing_is_asked_for(ctxs):
    out = T.find_facilities(ctxs["bob"])
    assert out["matched"] == 3
    assert out["matched_instruments"] == 12
    assert all(f["distance_km"] is None for f in out["facilities"] if "distance_km" in f)


def test_half_a_location_is_refused(ctxs):
    """One coordinate is not a place, and guessing the other one would be an invention."""
    with pytest.raises(ToolError) as exc:
        T.find_facilities(ctxs["bob"], near_latitude=51.5)
    assert exc.value.code == "invalid_params"


def test_an_impossible_coordinate_is_refused(ctxs):
    with pytest.raises(ToolError) as exc:
        T.find_facilities(ctxs["bob"], near_latitude=910.0, near_longitude=-0.13)
    assert exc.value.code == "invalid_params"


def test_a_coordinate_that_is_not_a_number_is_refused(ctxs):
    with pytest.raises(ToolError) as exc:
        T.find_facilities(ctxs["bob"], near_latitude="over there", near_longitude="-0.13")
    assert exc.value.code == "invalid_params"


def test_the_facilities_card_is_built_from_the_returned_rows(ctxs):
    out = T.find_facilities(
        ctxs["bob"], technique="light sheet microscopy",
        near_latitude=MASSSPEC[0], near_longitude=MASSSPEC[1],
    )
    card = out["card"]
    _assert_card_contract(card, "facilities")
    assert card["subtitle"] == "Nearest first"
    assert [item["title"] for item in card["items"]] == [
        f["name"] for f in out["facilities"]
    ]
    item = card["items"][0]
    # Distance is the answer to "where is the nearest core", so it sits in `value` — the
    # field the UI sets apart — rather than buried in meta beside the address.
    assert item["value"] == f"{out['facilities'][0]['distance_km']} km"
    # Light Sheet LS7 is under maintenance: the card must say so rather than let a reader
    # assume that being listed means being bookable.
    assert item["badges"] == [{"text": "Light Sheet LS7 · maintenance", "tone": "warn"}]


def test_an_available_instrument_carries_the_ok_tone(ctxs):
    out = T.find_facilities(ctxs["bob"], technique="cryo-EM")
    assert out["card"]["items"][0]["badges"] == [
        {"text": "Cryo-EM Titan · available", "tone": "ok"}
    ]


# --- 17. recommend_instrument -------------------------------------------------------


def test_an_exact_technique_match_is_ranked_first(ctxs):
    out = T.recommend_instrument(ctxs["bob"], goal="I want to do single-cell RNA-seq")
    top = out["matches"][0]
    assert top["instrument"] == "NovaSeq X"
    assert top["facility"] == "Genomics Core"
    assert top["campus"] == "North Campus"
    assert top["hourly_rate"] == 120.0
    assert top["status"] == "available"
    assert "RNA-seq" in top["techniques"]
    assert top["score"] > out["matches"][1]["score"], "an exact match must outrank overlap"


def test_why_matched_names_the_evidence_not_just_a_score(ctxs):
    """A ranking a reader cannot audit is indistinguishable from a guess."""
    out = T.recommend_instrument(ctxs["bob"], goal="cryo-EM of a protein complex")
    top = out["matches"][0]
    assert top["instrument"] == "Cryo-EM Titan"
    assert "exact technique match: cryo-EM" in top["why_matched"]
    assert any("protein" in reason for reason in top["why_matched"])


def test_a_goal_in_plain_english_still_finds_the_right_kit(ctxs):
    """"I want to look at live cells" has no technique in it — only overlap can answer."""
    out = T.recommend_instrument(ctxs["bob"], goal="I want to look at live cells")
    assert out["matched"] > 0
    top_three = {m["instrument"] for m in out["matches"][:3]}
    assert top_three == {"Confocal C2", "Confocal C3", "Spinning Disk SD1"}
    assert all("live cells" in m["sample_types"] for m in out["matches"][:3])


def test_an_unavailable_instrument_is_still_recommended_carrying_its_status(ctxs):
    """It exists and it cannot be booked — the caller has to be told both.

    Dropping it answers "what can I book right now?", which is a different question from
    "which instrument does this?", and leaves a scientist believing the capability is not
    here at all.
    """
    out = T.recommend_instrument(ctxs["bob"], goal="light sheet microscopy of cleared tissue")
    top = out["matches"][0]
    assert top["instrument"] == "Light Sheet LS7"
    assert top["status"] == "maintenance"
    assert top["bookable"] is False
    badge = out["card"]["items"][0]["badges"][0]
    assert badge == {"text": "maintenance", "tone": "warn"}


def test_an_offline_instrument_is_surfaced_too(ctxs):
    out = T.recommend_instrument(ctxs["bob"], goal="metabolomics")
    assert [m["instrument"] for m in out["matches"]] == ["Q-TOF 6546"]
    assert out["matches"][0]["status"] == "offline"
    assert out["matches"][0]["bookable"] is False


def test_a_bookable_instrument_wins_a_tie(ctxs):
    """Equal evidence, so the tie-break is what the caller can actually act on.

    Three instruments take organoids and one of them is in pieces on a bench. It is still
    listed — it just does not go first, and alphabetical order would have put it there.
    """
    out = T.recommend_instrument(ctxs["bob"], goal="organoids")
    tied = [m for m in out["matches"] if m["score"] == out["matches"][0]["score"]]
    assert {m["instrument"] for m in tied} == {
        "Confocal C3", "Light Sheet LS7", "Spinning Disk SD1"
    }
    assert [m["instrument"] for m in tied] == [
        "Confocal C3", "Spinning Disk SD1", "Light Sheet LS7"
    ]


def test_scoring_is_deterministic(ctxs):
    """No model is consulted, so the same goal must produce the identical ranking.

    This repo has been burned three times by prompt tweaks that regressed a passing case.
    A recommendation that moves when a sentence is reworded cannot be defended to someone
    about to spend £145 an hour on the result.
    """
    first = T.recommend_instrument(ctxs["bob"], goal="cryo-EM of a protein complex")
    second = T.recommend_instrument(ctxs["bob"], goal="cryo-EM of a protein complex")
    assert first == second
    assert [(m["instrument"], m["score"]) for m in first["matches"]] == [
        (m["instrument"], m["score"]) for m in second["matches"]
    ]


def test_the_framing_around_a_goal_does_not_change_the_answer(ctxs):
    """"metabolomics" and "I need to run some metabolomics" are the same request."""
    bare = T.recommend_instrument(ctxs["bob"], goal="metabolomics")
    padded = T.recommend_instrument(ctxs["bob"], goal="I need to run some metabolomics")
    assert [m["instrument"] for m in bare["matches"]] == [
        m["instrument"] for m in padded["matches"]
    ]


def test_a_nonsense_goal_recommends_nothing(ctxs):
    """Verified or silent: nothing scores, so nothing is offered."""
    out = T.recommend_instrument(ctxs["bob"], goal="quantum banana zeppelin tuba")
    assert out["matched"] == 0
    assert out["matches"] == []
    assert out["card"]["items"] == []
    assert out["card"]["footer"] == "Nothing on record matches that goal."


def test_an_empty_goal_is_refused(ctxs):
    with pytest.raises(ToolError) as exc:
        T.recommend_instrument(ctxs["bob"], goal="   ")
    assert exc.value.code == "invalid_params"


def test_a_sample_type_narrows_the_recommendation(ctxs):
    """An instrument that does not take the sample is not a recommendation."""
    out = T.recommend_instrument(ctxs["bob"], goal="imaging", sample_type="cleared tissue")
    assert [m["instrument"] for m in out["matches"]] == ["Light Sheet LS7"]
    assert out["excluded_by_sample_type"] > 0, "the filter reports what it removed"
    assert all("cleared tissue" in m["sample_types"] for m in out["matches"])


def test_a_sample_type_that_nothing_takes_returns_nothing(ctxs):
    out = T.recommend_instrument(ctxs["bob"], goal="imaging", sample_type="moon rock")
    assert out["matches"] == []
    assert out["matched"] == 0


def test_the_instruments_card_is_built_from_the_ranked_rows(ctxs):
    out = T.recommend_instrument(ctxs["bob"], goal="proteomics by LC-MS/MS")
    card = out["card"]
    _assert_card_contract(card, "instruments")
    assert card["subtitle"] == "proteomics by LC-MS/MS"
    assert [item["title"] for item in card["items"]] == [
        m["instrument"] for m in out["matches"]
    ]
    top_item, top_match = card["items"][0], out["matches"][0]
    assert top_match["instrument"] == "Orbitrap Exploris"
    assert f"${top_match['hourly_rate']:.2f}/h" in top_item["meta"], "the rate goes in meta"
    assert top_match["room"] in top_item["meta"]
    assert top_item["subtitle"] == top_match["facility"]
    assert top_item["value"] == f"score {top_match['score']}"
    for reason in top_match["why_matched"]:
        assert reason in top_item["meta"], "the evidence travels with the card"


# --- registry + dispatch ------------------------------------------------------------


def test_the_registry_holds_seventeen_contiguous_tools():
    """Mirrors tests/test_tools.py: 15 became 17 when discovery was added (16, 17)."""
    assert len(T.TOOLS) == 17
    assert sorted(spec.number for spec in T.TOOLS.values()) == list(range(1, 18))


def test_both_discovery_tools_are_registered_as_read_tools():
    for name in ("find_facilities", "recommend_instrument"):
        spec = T.TOOLS[name]
        assert spec.write is False
        assert spec.tier == "T0"
        assert name in T.READ_TOOLS
        assert spec.description and spec.params


def test_both_dispatch_through_call(ctxs):
    """The MCP server and the agent both arrive through `call`, so both are exercised."""
    assert T.call(ctxs["bob"], "find_facilities", {"technique": "MALDI-TOF"})["matched"] == 1
    assert T.call(ctxs["bob"], "recommend_instrument", {"goal": "MALDI-TOF"})["matched"] >= 1


def test_a_plain_user_may_use_both_tools(ctxs):
    """T0: a facility directory is public information — no lab or facility filter."""
    assert T.find_facilities(ctxs["bob"])["matched"] == 3
    assert T.recommend_instrument(ctxs["bob"], goal="sequencing")["matched"] > 0


def test_a_goal_argument_is_named_in_a_sentence_when_it_is_missing(ctxs):
    """Argument names reach users through `call`, so they are spelled as English."""
    with pytest.raises(ToolError) as exc:
        T.call(ctxs["bob"], "recommend_instrument", {})
    assert exc.value.code == "invalid_params"
    assert "_" not in exc.value.message, "schema spelling must not reach the reader"
