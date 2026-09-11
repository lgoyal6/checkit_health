"""The response layer is where the product's core promise is kept or lost.

Checkit says it assembles evidence and a human decides. A counter-post is a
verdict, published. These tests pin down the constraints that keep that promise
so a future change cannot quietly remove one.
"""

import pytest

import config
import response as rl

EVIDENCE = [
    {"id": "pubmed:1", "title": "Chlorine dioxide ingestion", "passage": "No benefit.",
     "url": "https://example.org/1", "publisher": "PubMed", "published_at": "2021"},
    {"id": "trial:2", "title": "A trial", "passage": "No effect observed.",
     "url": "https://example.org/2", "publisher": "ClinicalTrials", "published_at": "2020"},
]


# --- the evidence gate -----------------------------------------------------


def test_a_draft_is_refused_when_evidence_is_insufficient():
    """A tool that writes rebuttals for claims it has no evidence against is a
    misinformation generator."""
    with pytest.raises(rl.ResponseRefusal) as excinfo:
        rl.check_preconditions("insufficient", EVIDENCE, "peaking")
    assert excinfo.value.reason == "insufficient_evidence"


def test_a_draft_is_refused_when_the_claim_is_actually_supported():
    with pytest.raises(rl.ResponseRefusal) as excinfo:
        rl.check_preconditions("supported", EVIDENCE, "peaking")
    assert excinfo.value.reason == "insufficient_evidence"


def test_a_draft_is_refused_when_no_sources_are_attached():
    with pytest.raises(rl.ResponseRefusal) as excinfo:
        rl.check_preconditions("contradicted", [], "peaking")
    assert excinfo.value.reason == "no_sources"


def test_contradicted_and_mixed_are_the_only_states_that_allow_drafting():
    assert config.RESPONSE_ALLOWED_EVIDENCE_STATES == {"contradicted", "mixed"}
    assert rl.check_preconditions("contradicted", EVIDENCE, "peaking") == rl.DEBUNK
    assert rl.check_preconditions("mixed", EVIDENCE, "peaking") == rl.DEBUNK


def test_drafting_can_be_disabled_entirely(monkeypatch):
    monkeypatch.setattr(config, "RESPONSE_ENABLED", False)
    with pytest.raises(rl.ResponseRefusal) as excinfo:
        rl.check_preconditions("contradicted", EVIDENCE, "peaking")
    assert excinfo.value.reason == "response_drafting_disabled"


# --- mode follows the lifecycle, not the caller ----------------------------


def test_an_emerging_rumor_gets_a_prebunk_not_a_debunk():
    """Restating a myth to an audience that has not seen it spreads it."""
    assert rl.check_preconditions("contradicted", EVIDENCE, "emerging") == rl.PREBUNK
    assert rl.check_preconditions("contradicted", EVIDENCE, "accelerating") == rl.PREBUNK


def test_a_widely_seen_rumor_gets_a_direct_debunk():
    assert rl.check_preconditions("contradicted", EVIDENCE, "peaking") == rl.DEBUNK


def test_a_fading_rumor_gets_no_response_at_all():
    """Responding to a dying rumor revives it."""
    for state in ("declining", "dormant"):
        with pytest.raises(rl.ResponseRefusal) as excinfo:
            rl.check_preconditions("contradicted", EVIDENCE, state)
        assert excinfo.value.reason == "narrative_declining"


def test_an_unknown_lifecycle_state_falls_back_to_debunk():
    assert rl.mode_for_lifecycle("something-new") == rl.DEBUNK
    assert rl.mode_for_lifecycle(None) == rl.DEBUNK


# --- citation validation ---------------------------------------------------


def test_invented_citations_are_dropped():
    draft = rl._normalize(
        {"fact": "Bleach is not a treatment.", "myth": "Bleach cures autism.",
         "citations": ["pubmed:1", "pubmed:9999", "made-up"]},
        EVIDENCE, rl.DEBUNK,
    )
    assert draft.citations == ["pubmed:1"]
    assert sorted(draft.dropped_citations) == ["made-up", "pubmed:9999"]


def test_a_draft_citing_nothing_real_is_discarded_not_shown():
    with pytest.raises(rl.ResponseRefusal) as excinfo:
        rl._normalize({"fact": "x", "citations": ["invented:1"]}, EVIDENCE, rl.DEBUNK)
    assert excinfo.value.reason == "no_valid_citations"


def test_a_draft_with_no_citations_at_all_is_discarded():
    with pytest.raises(rl.ResponseRefusal):
        rl._normalize({"fact": "x", "citations": []}, EVIDENCE, rl.DEBUNK)


# --- prebunk never restates the myth ---------------------------------------


def test_a_prebunk_drops_the_myth_even_if_the_model_supplies_one():
    """The whole point of pre-bunking is not naming the rumor. This is enforced
    after generation rather than trusted to the prompt."""
    draft = rl._normalize(
        {"fact": "Chlorine dioxide is not a treatment.",
         "myth": "Bleach cures autism.",
         "fallacy_name": "anecdote as evidence",
         "citations": ["pubmed:1"]},
        EVIDENCE, rl.PREBUNK,
    )
    assert draft.myth == ""
    assert draft.mode == rl.PREBUNK


def test_a_debunk_keeps_the_myth_stated_once():
    draft = rl._normalize(
        {"fact": "Chlorine dioxide is not a treatment.",
         "myth": "Bleach cures autism.", "citations": ["pubmed:1"]},
        EVIDENCE, rl.DEBUNK,
    )
    assert draft.myth == "Bleach cures autism."


# --- protocol shape --------------------------------------------------------


def test_the_draft_carries_every_debunking_handbook_slot():
    draft = rl._normalize({"citations": ["pubmed:1"]}, EVIDENCE, rl.DEBUNK)
    for slot in ("fact", "warning", "myth", "fallacy_name", "explanation",
                 "replacement", "reinforcement"):
        assert hasattr(draft, slot)


def test_tone_guidance_travels_with_every_draft():
    draft = rl._normalize({"citations": ["pubmed:1"]}, EVIDENCE, rl.DEBUNK)
    joined = " ".join(draft.tone_notes).lower()
    assert "gullible" in joined
    assert "lead with the fact" in joined


def test_the_suggested_post_is_length_capped():
    draft = rl._normalize(
        {"suggested_post": "x" * 1000, "citations": ["pubmed:1"]},
        EVIDENCE, rl.DEBUNK,
    )
    assert len(draft.suggested_post) <= 400


def test_both_modes_name_a_published_protocol():
    assert "debunking-handbook" in rl.PROTOCOLS[rl.DEBUNK]
    assert "inoculation" in rl.PROTOCOLS[rl.PREBUNK]


# --- export ----------------------------------------------------------------


def test_exported_text_says_a_human_must_send_it():
    draft = rl._normalize(
        {"fact": "Chlorine dioxide is not a treatment.",
         "suggested_post": "Bleach is not a treatment for autism.",
         "citations": ["pubmed:1"]},
        EVIDENCE, rl.DEBUNK,
    ).model_dump()
    text = rl.render_plain_text(draft, "Bleach cures autism")

    assert "Bleach cures autism" in text
    assert "does not publish" in text
    assert "pubmed:1" in text


def test_exported_text_omits_empty_slots():
    draft = rl._normalize(
        {"fact": "Only this is filled.", "citations": ["pubmed:1"]},
        EVIDENCE, rl.DEBUNK,
    ).model_dump()
    text = rl.render_plain_text(draft)
    assert "FACT: Only this is filled." in text
    assert "WHY IT IS WRONG" not in text
