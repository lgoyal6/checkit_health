from datetime import datetime, timezone

from intelligence import (
    adverse_event_routing,
    assess_claim,
    cluster_id,
    detect_language,
    evidence_quality,
    velocity,
)


def test_word_order_variants_share_a_cluster():
    assert cluster_id("Vitamin C cures cancer") == cluster_id("Cancer cured by vitamin C")


def test_language_detection_is_conservative_and_discloses_no_translation():
    signal = detect_language("La vacuna causa daño")
    assert signal["code"] == "es"
    assert signal["translation_applied"] is False


def test_adverse_event_language_routes_to_human_review():
    signal = adverse_event_routing("I had a seizure after taking this drug")
    assert signal["detected"] is True
    assert signal["route"] == "urgent_human_review"


def test_evidence_quality_does_not_treat_no_match_as_false():
    signal = evidence_quality(None, None)
    assert signal["level"] == "unreviewed"
    assert "No published evidence match" in signal["reason"]


def test_velocity_uses_interactions_over_age():
    now = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)
    signal = velocity(80, 20, "2026-07-23T10:00:00Z", now=now)
    assert signal["reach"] == 100
    assert signal["interactions_per_hour"] == 50


def test_assessment_preserves_human_final_judgment():
    result = assess_claim("This drug caused an allergic reaction", 0.75)
    assert result["escalation"]["human_review_required"] is True
    assert result["audit"]["final_judgment"] == "human"
