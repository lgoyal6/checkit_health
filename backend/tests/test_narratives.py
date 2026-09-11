from datetime import datetime, timedelta, timezone

import narratives as nar


def _at(hours: float) -> str:
    base = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
    return (base + timedelta(hours=hours)).isoformat()


# --- embedding -------------------------------------------------------------


def test_embeddings_are_normalized_and_fixed_width():
    import config

    (vector,) = nar.embed(["The MMR vaccine causes autism"])
    assert len(vector) == config.EMBEDDING_DIMENSIONS
    assert abs(sum(v * v for v in vector) - 1.0) < 1e-6


def test_embedding_is_deterministic():
    first, second = nar.embed(["Ivermectin cures COVID", "Ivermectin cures COVID"])
    assert nar.cosine(first, second) > 0.999


def test_unrelated_claims_are_far_apart():
    a, b = nar.embed(["Vaccines cause autism", "Fluoride lowers IQ in children"])
    assert nar.cosine(a, b) < 0.2


def test_cosine_handles_mismatched_and_empty_vectors():
    assert nar.cosine([], [1.0]) == 0.0
    assert nar.cosine([1.0, 0.0], [1.0]) == 0.0


# --- assignment ------------------------------------------------------------


def test_assignment_starts_a_new_narrative_when_nothing_is_close():
    (vector,) = nar.embed(["Raw milk is safer than pasteurized milk"])
    decision = nar.assign(vector, [])
    assert decision.is_new is True
    assert decision.narrative_id is None


def test_assignment_joins_the_nearest_narrative_above_threshold():
    (existing,) = nar.embed(["Bleach cures autism"])
    (incoming,) = nar.embed(["Drinking bleach cures autism"])
    decision = nar.assign(
        incoming, [nar.Candidate("n1", existing, 1)], threshold=0.6
    )
    assert decision.is_new is False
    assert decision.narrative_id == "n1"
    assert decision.similarity >= 0.6


def test_assignment_picks_the_closest_of_several_candidates():
    bleach, fluoride, incoming = nar.embed([
        "Bleach cures autism",
        "Fluoride lowers IQ in children",
        "Drinking bleach cures autism",
    ])
    decision = nar.assign(
        incoming,
        [nar.Candidate("fluoride", fluoride, 1), nar.Candidate("bleach", bleach, 1)],
        threshold=0.5,
    )
    assert decision.narrative_id == "bleach"


def test_a_high_threshold_refuses_to_merge_merely_similar_claims():
    """Under-merging is the safe failure, so the threshold must be able to
    hold two vocabulary-sharing claims apart."""
    autism, seizures = nar.embed([
        "The MMR vaccine causes autism", "The MMR vaccine causes seizures"
    ])
    decision = nar.assign(seizures, [nar.Candidate("n1", autism, 1)], threshold=0.95)
    assert decision.is_new is True


def test_merge_centroid_stays_normalized_and_moves_toward_the_new_member():
    a, b = nar.embed(["Bleach cures autism", "Drinking bleach cures autism"])
    merged = nar.merge_centroid(a, 1, b)
    assert abs(sum(v * v for v in merged) - 1.0) < 1e-6
    assert nar.cosine(merged, b) > nar.cosine(a, b)


def test_narrative_id_is_stable_for_the_same_claim():
    assert nar.narrative_id_for("Bleach cures autism") == nar.narrative_id_for(
        "  bleach cures autism  "
    )


# --- labeling --------------------------------------------------------------


def test_label_prefers_a_concise_phrasing_that_carries_the_common_words():
    label = nar.label_for([
        "Bleach cures autism",
        "Drinking bleach reportedly cures autism in children, say parents",
        "Bleach cures autism",
    ])
    assert "bleach" in label.lower()
    assert "autism" in label.lower()
    assert len(label) < 60


def test_label_handles_an_empty_group():
    assert nar.label_for([]) == "Unlabeled narrative"


def test_long_labels_are_truncated():
    label = nar.label_for(["word " * 80])
    assert len(label) <= 91


# --- trajectory ------------------------------------------------------------


def test_trajectory_is_empty_without_snapshots():
    traj = nar.trajectory([])
    assert traj.observations == 0
    assert traj.growth_per_hour == 0.0


def test_trajectory_sums_across_posts_and_carries_values_forward():
    """Posts are observed on a schedule, so the total at each observation must
    include the last known value of posts not re-observed in that bucket."""
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": _at(0), "like_count": 10, "retweet_count": 0},
        {"post_id": "b", "observed_at": _at(0), "like_count": 5, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(2), "like_count": 30, "retweet_count": 0},
        {"post_id": "b", "observed_at": _at(2), "like_count": 15, "retweet_count": 0},
    ])
    assert traj.observations == 2
    assert traj.previous_reach == 15
    assert traj.reach == 45
    assert traj.growth_per_hour == 15.0


def test_trajectory_ignores_unparseable_timestamps():
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": "not-a-date", "like_count": 10},
        {"post_id": "a", "observed_at": _at(0), "like_count": 10},
    ])
    assert traj.observations == 1


def test_growth_never_goes_negative_when_engagement_drops():
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": _at(0), "like_count": 100, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(1), "like_count": 40, "retweet_count": 0},
    ])
    assert traj.growth_per_hour == 0.0


# --- lifecycle -------------------------------------------------------------


def test_a_spiking_narrative_is_accelerating():
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": _at(0), "like_count": 10, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(1), "like_count": 60, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(2), "like_count": 400, "retweet_count": 0},
    ])
    state = nar.lifecycle_state(
        traj, first_seen_at=_at(-100), last_seen_at=_at(2),
        now=datetime.fromisoformat(_at(2)),
    )
    assert state == "accelerating"


def test_trivial_growth_is_not_reported_as_accelerating():
    """A narrative that gained a handful of likes overnight has an infinite
    acceleration ratio, which must not read the same as a real spike."""
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": _at(0), "like_count": 60, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(6), "like_count": 64, "retweet_count": 0},
    ])
    state = nar.lifecycle_state(
        traj, first_seen_at=_at(-100), last_seen_at=_at(6),
        now=datetime.fromisoformat(_at(6)),
    )
    assert state == "watching"


def test_a_narrative_with_no_recent_activity_is_dormant():
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": _at(0), "like_count": 10, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(1), "like_count": 900, "retweet_count": 0},
    ])
    state = nar.lifecycle_state(
        traj, first_seen_at=_at(0), last_seen_at=_at(1),
        now=datetime.fromisoformat(_at(200)),
    )
    assert state == "dormant"


def test_a_decelerating_narrative_is_declining():
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": _at(0), "like_count": 0, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(1), "like_count": 500, "retweet_count": 0},
        {"post_id": "a", "observed_at": _at(2), "like_count": 520, "retweet_count": 0},
    ])
    state = nar.lifecycle_state(
        traj, first_seen_at=_at(-100), last_seen_at=_at(2),
        now=datetime.fromisoformat(_at(2)),
    )
    assert state == "declining"


def test_a_brand_new_narrative_with_one_observation_is_emerging():
    traj = nar.trajectory([
        {"post_id": "a", "observed_at": _at(0), "like_count": 10, "retweet_count": 0},
    ])
    state = nar.lifecycle_state(
        traj, first_seen_at=_at(0), last_seen_at=_at(0),
        now=datetime.fromisoformat(_at(1)),
    )
    assert state == "emerging"


# --- calibration -----------------------------------------------------------


def test_threshold_evaluation_counts_merges_correctly():
    a, b, c = nar.embed([
        "Bleach cures autism", "Drinking bleach cures autism",
        "Fluoride lowers IQ in children",
    ])
    result = nar.evaluate_threshold([(a, b, True), (a, c, False)], threshold=0.5)
    assert result["true_positives"] == 1
    assert result["true_negatives"] == 1
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0


def test_fbeta_weights_precision_above_recall():
    """A wrong merge corrupts a narrative; a missed merge only duplicates a
    row. Selection must reflect that asymmetry."""
    a, b, c = nar.embed([
        "Bleach cures autism", "Drinking bleach cures autism",
        "Fluoride lowers IQ in children",
    ])
    # One correct merge, one wrong merge: recall is perfect, precision is not.
    pairs = [(a, b, True), (a, c, False)]
    loose = nar.evaluate_threshold(pairs, threshold=0.0, beta=0.5)
    assert loose["recall"] == 1.0
    assert loose["fbeta"] < loose["f1"]


def test_separation_flags_an_embedding_that_cannot_cluster():
    a, b = nar.embed(["Bleach cures autism", "Drinking bleach cures autism"])
    # Same similarity for a "same" pair and a "different" pair: no signal.
    verdict = nar.separation([(a, b, True), (a, b, False)])
    assert verdict["auc"] == 0.5
    assert verdict["verdict"] == "unusable for semantic clustering"


def test_separation_reports_clean_separation_when_it_exists():
    a, b, c = nar.embed([
        "Bleach cures autism", "Drinking bleach cures autism",
        "Fluoride lowers IQ in children",
    ])
    verdict = nar.separation([(a, b, True), (a, c, False)])
    assert verdict["auc"] == 1.0
    assert verdict["verdict"] == "clean separation"


def test_sweep_ignores_thresholds_that_merge_almost_nothing():
    """A threshold that merges nothing has perfect precision and is useless."""
    a, b, c = nar.embed([
        "Bleach cures autism", "Drinking bleach cures autism",
        "Fluoride lowers IQ in children",
    ])
    sweep = nar.sweep_threshold([(a, b, True), (a, c, False)], 0.1, 0.99, 0.05)
    assert sweep["best"]["recall"] >= 0.2
    assert "separation" in sweep


def test_bundled_labeled_pairs_stay_loadable_and_balanced():
    """The calibration set is a fixture others depend on; guard its shape."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data" / "claim_pairs.json"
    pairs = json.loads(path.read_text(encoding="utf-8"))["pairs"]
    same = [p for p in pairs if p["same"]]
    different = [p for p in pairs if not p["same"]]
    assert len(same) >= 25 and len(different) >= 25
    assert all(p["a"].strip() and p["b"].strip() for p in pairs)
