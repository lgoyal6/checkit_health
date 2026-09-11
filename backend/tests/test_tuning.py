import random

import config
import tuning


def row(reach, harm, uncertainty, status, selected_by="rank", score=50.0):
    return {
        "post_id": f"p{reach}{harm}{uncertainty}{status}{selected_by}",
        "reach_component": reach, "harm_component": harm,
        "uncertainty_component": uncertainty, "score": score,
        "review_status": status, "selected_by": selected_by,
    }


def synthetic(n=160, harm_weight=0.7, seed=11, exploration_every=5):
    """Analysts in this world care mostly about harm, then reach.

    The fitter should recover roughly that ordering from the decisions alone.
    """
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        reach, harm = rng.random(), rng.choice([0.25, 1.0])
        unc = rng.random() * 0.5
        keep = rng.random() < (harm_weight * harm + (1 - harm_weight) * reach)
        rows.append(row(
            reach, harm, unc,
            "accepted" if keep else "rejected",
            "exploration" if i % exploration_every == 0 else "rank",
        ))
    return rows


# --- labeling --------------------------------------------------------------


def test_only_decided_claims_become_labels():
    rows = [
        row(0.5, 1.0, 0.2, "accepted"),
        row(0.5, 1.0, 0.2, "rejected"),
        row(0.5, 1.0, 0.2, "unreviewed"),
        row(0.5, 1.0, 0.2, ""),
    ]
    samples = tuning.to_samples(rows)
    assert len(samples) == 2
    assert sorted(s.label for s in samples) == [0, 1]


def test_engaging_with_a_claim_counts_as_worth_surfacing():
    """in_review and needs_evidence mean the analyst took it seriously; only
    an explicit rejection says the triage was wrong to raise it."""
    for status in ("accepted", "in_review", "needs_evidence"):
        (sample,) = tuning.to_samples([row(0.5, 1.0, 0.2, status)])
        assert sample.label == 1
    (rejected,) = tuning.to_samples([row(0.5, 1.0, 0.2, "rejected")])
    assert rejected.label == 0


# --- fitting ---------------------------------------------------------------


def test_the_fitter_recovers_the_signal_analysts_actually_followed():
    result = tuning.fit(synthetic())
    weights = result.weights
    assert weights["potential_harm"] > weights["reach"]
    assert weights["potential_harm"] > weights["uncertainty"]
    assert abs(sum(weights.values()) - 1.0) < 0.01


def test_fitted_weights_stay_non_negative_and_explainable():
    """A component that pushes priority down would make 'why is this ranked
    high' incoherent for an analyst, and with this few labels a negative sign
    is more likely noise than signal."""
    result = tuning.fit(synthetic())
    assert all(value >= 0 for value in result.weights.values())


def test_a_candidate_that_beats_the_baseline_is_recommended():
    result = tuning.fit(synthetic())
    assert result.recommendation == "ship_candidate"
    assert result.candidate["auc"] > result.baseline["auc"]
    assert result.weights_version != config.WEIGHTS_VERSION


def test_nothing_is_recommended_without_enough_labels():
    result = tuning.fit(synthetic(n=10))
    assert result.recommendation == "collect_more"
    assert any("minimum" in w for w in result.warnings)


def test_no_decisions_at_all_keeps_the_live_weights():
    result = tuning.fit([])
    assert result.recommendation == "keep"
    assert result.weights == config.PRIORITY_WEIGHTS
    assert result.samples == 0


# --- selection bias --------------------------------------------------------


def test_metrics_are_computed_on_the_exploration_slice_only():
    """Labels from a score-ranked queue can only confirm the ranking that
    produced them, so evaluation has to use the randomly surfaced sample."""
    rows = synthetic(n=100, exploration_every=4)
    result = tuning.fit(rows)
    assert result.exploration_samples > 0
    assert result.exploration_samples < result.samples
    assert result.baseline["samples"] == result.exploration_samples


def test_the_absence_of_an_exploration_sample_is_warned_about_loudly():
    rows = [
        row(rng / 100, 1.0 if rng % 2 else 0.25, 0.2,
            "accepted" if rng % 3 else "rejected", "rank")
        for rng in range(60)
    ]
    result = tuning.fit(rows)
    assert result.exploration_samples == 0
    assert any("exploration" in w for w in result.warnings)
    assert result.recommendation == "collect_more"


def test_too_few_exploration_labels_still_warns():
    result = tuning.fit(synthetic(n=60, exploration_every=30))
    assert any("exploration slot" in w for w in result.warnings)


# --- metrics ---------------------------------------------------------------


def test_precision_at_k_measures_the_top_of_the_queue():
    samples = tuning.to_samples([
        row(1.0, 1.0, 0.5, "accepted"),
        row(0.9, 1.0, 0.5, "accepted"),
        row(0.1, 0.25, 0.1, "rejected"),
        row(0.0, 0.25, 0.0, "rejected"),
    ])
    result = tuning.precision_at_k(config.PRIORITY_WEIGHTS, samples, k=2)
    assert result["precision"] == 1.0
    assert result["considered"] == 2


def test_precision_at_k_handles_k_larger_than_the_sample():
    samples = tuning.to_samples([row(1.0, 1.0, 0.5, "accepted")])
    result = tuning.precision_at_k(config.PRIORITY_WEIGHTS, samples, k=50)
    assert result["considered"] == 1
    assert result["precision"] == 1.0


def test_precision_at_k_on_no_samples_is_none_not_zero():
    result = tuning.precision_at_k(config.PRIORITY_WEIGHTS, [], k=20)
    assert result["precision"] is None


def test_auc_is_one_when_the_ranking_is_perfect():
    samples = tuning.to_samples([
        row(1.0, 1.0, 0.5, "accepted"),
        row(0.0, 0.25, 0.0, "rejected"),
    ])
    assert tuning.auc(config.PRIORITY_WEIGHTS, samples) == 1.0


def test_auc_is_none_when_one_class_is_missing():
    samples = tuning.to_samples([row(1.0, 1.0, 0.5, "accepted")])
    assert tuning.auc(config.PRIORITY_WEIGHTS, samples) is None


def test_fitting_never_mutates_the_live_weights():
    before = dict(config.PRIORITY_WEIGHTS)
    tuning.fit(synthetic())
    assert config.PRIORITY_WEIGHTS == before
