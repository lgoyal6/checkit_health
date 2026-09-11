"""Fit the review-priority weights against real analyst decisions.

The three weights in ``config.PRIORITY_WEIGHTS`` started as hand-set
heuristics. This module replaces them with numbers measured against what
analysts actually did, and it exists mostly to make that replacement *honest*.

Three things make it honest:

**Selection bias is handled, not ignored.** Analysts see claims because the
score ranked them highly. Fitting on that history teaches the model the
ranking it already has. So a fraction of the queue is drawn at random
(``config.EXPLORATION_RATE``, marked ``selected_by='exploration'``) and every
reported metric is computed on that slice only. It is a smaller number and a
truthful one.

**The model is deliberately tiny.** Three features and, realistically, a few
hundred labels. Logistic regression with L2 regularization, fitted by plain
gradient descent, no dependency. Anything more expressive would overfit and
would be harder to explain to the health department relying on the ranking.

**Nothing changes silently.** Fitting produces a candidate ``weights_version``
and a comparison against the version in force. Shipping it is a separate,
deliberate act, and old scores stay attributable to the weights that produced
them.

The headline metric is precision@k: of the top k claims the system put in front
of an analyst, how many did the analyst agree were worth reviewing. One number,
honest, and it means something to a non-technical buyer.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config

FEATURES = ("reach", "potential_harm", "uncertainty")

# An analyst decision counts as "this was worth surfacing" when they engaged
# with it as a real claim. 'rejected' means the triage was wrong to raise it.
POSITIVE_STATUSES = {"accepted", "in_review", "needs_evidence"}
NEGATIVE_STATUSES = {"rejected"}

MIN_LABELS = 30
MIN_EXPLORATION_LABELS = 15


@dataclass
class Sample:
    post_id: str
    features: Tuple[float, float, float]
    label: int
    selected_by: str
    score: float


@dataclass
class FitResult:
    weights: Dict[str, float]
    weights_version: str
    samples: int
    exploration_samples: int
    baseline: Dict[str, Any] = field(default_factory=dict)
    candidate: Dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "weights": self.weights,
            "weights_version": self.weights_version,
            "samples": self.samples,
            "exploration_samples": self.exploration_samples,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "recommendation": self.recommendation,
            "warnings": self.warnings,
        }


def to_samples(rows: Sequence[Dict[str, Any]]) -> List[Sample]:
    """Turn stored score/outcome joins into labeled training rows."""
    samples = []
    for row in rows:
        status = (row.get("review_status") or "").lower()
        if status in POSITIVE_STATUSES:
            label = 1
        elif status in NEGATIVE_STATUSES:
            label = 0
        else:
            continue
        samples.append(Sample(
            post_id=row.get("post_id") or "",
            features=(
                float(row.get("reach_component") or 0.0),
                float(row.get("harm_component") or 0.0),
                float(row.get("uncertainty_component") or 0.0),
            ),
            label=label,
            selected_by=(row.get("selected_by") or "rank"),
            score=float(row.get("score") or 0.0),
        ))
    return samples


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def fit_logistic(
    samples: Sequence[Sample],
    l2: float = 1.0,
    learning_rate: float = 0.5,
    epochs: int = 2000,
) -> Tuple[List[float], float]:
    """L2-regularized logistic regression by gradient descent.

    Strong regularization by default because the label budget is small; the
    point is a stable ordering, not a tight fit.
    """
    n_features = len(FEATURES)
    weights = [0.0] * n_features
    bias = 0.0
    n = len(samples)
    if n == 0:
        return weights, bias

    for _ in range(epochs):
        grad_w = [0.0] * n_features
        grad_b = 0.0
        for sample in samples:
            z = bias + sum(w * x for w, x in zip(weights, sample.features))
            error = _sigmoid(z) - sample.label
            for i, x in enumerate(sample.features):
                grad_w[i] += error * x
            grad_b += error
        for i in range(n_features):
            grad_w[i] = grad_w[i] / n + l2 * weights[i] / n
            weights[i] -= learning_rate * grad_w[i]
        bias -= learning_rate * (grad_b / n)
    return weights, bias


def _normalize_weights(raw: Sequence[float]) -> Dict[str, float]:
    """Map fitted coefficients onto the same non-negative, summing-to-one shape
    the hand-set weights have, so the score stays comparable and explainable.

    A negative coefficient is clamped to zero rather than kept: a component
    that pushes priority *down* would make the explanation incoherent to an
    analyst reading "why is this ranked high", and with this few labels a
    negative sign is more likely noise than signal.
    """
    clamped = [max(0.0, value) for value in raw]
    total = sum(clamped)
    if total <= 0:
        return dict(config.PRIORITY_WEIGHTS)
    return {name: round(value / total, 4) for name, value in zip(FEATURES, clamped)}


def score_with(weights: Dict[str, float], sample: Sample) -> float:
    total = sum(weights.values()) or 1.0
    raw = sum(
        weights.get(name, 0.0) * value
        for name, value in zip(FEATURES, sample.features)
    )
    return 100 * raw / total


def precision_at_k(
    weights: Dict[str, float], samples: Sequence[Sample], k: int = 20
) -> Dict[str, Any]:
    """Of the top k this ranking would surface, how many did analysts keep.

    This is the number to put on a slide. It answers the question a health
    department actually asks, which is not "what is your AUC" but "if I work
    your top twenty today, how much of my time do you waste".
    """
    if not samples:
        return {"k": k, "precision": None, "hits": 0, "considered": 0}
    ranked = sorted(samples, key=lambda s: score_with(weights, s), reverse=True)
    top = ranked[: min(k, len(ranked))]
    hits = sum(s.label for s in top)
    return {
        "k": k,
        "considered": len(top),
        "hits": hits,
        "precision": round(hits / len(top), 4) if top else None,
    }


def auc(weights: Dict[str, float], samples: Sequence[Sample]) -> Optional[float]:
    """Probability a kept claim outranks a rejected one."""
    positives = [score_with(weights, s) for s in samples if s.label == 1]
    negatives = [score_with(weights, s) for s in samples if s.label == 0]
    if not positives or not negatives:
        return None
    wins = ties = 0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return round((wins + 0.5 * ties) / (len(positives) * len(negatives)), 4)


def evaluate(
    weights: Dict[str, float], samples: Sequence[Sample], k: int = 20
) -> Dict[str, Any]:
    return {
        "weights": dict(weights),
        "auc": auc(weights, samples),
        "precision_at_k": precision_at_k(weights, samples, k=k),
        "samples": len(samples),
        "positive_rate": (
            round(sum(s.label for s in samples) / len(samples), 4) if samples else None
        ),
    }


def fit(
    rows: Sequence[Dict[str, Any]],
    k: int = 20,
    l2: float = 1.0,
    version_suffix: str = "",
) -> FitResult:
    """Fit candidate weights and compare them to the ones currently in force.

    Training uses every label available, because throwing away the ranked
    majority would leave nothing to fit. *Evaluation* uses only the exploration
    slice, because that is the only sample not shaped by the ranking under
    test. Both counts are reported so the gap is visible.
    """
    samples = to_samples(rows)
    exploration = [s for s in samples if s.selected_by == "exploration"]
    warnings: List[str] = []

    if len(samples) < MIN_LABELS:
        warnings.append(
            f"Only {len(samples)} labeled decisions; {MIN_LABELS} is the minimum "
            "before a fitted weight means anything. Keep reviewing."
        )
    if len(exploration) < MIN_EXPLORATION_LABELS:
        warnings.append(
            f"Only {len(exploration)} decisions came from the random exploration "
            f"slot ({MIN_EXPLORATION_LABELS} needed). Until then every metric "
            "below is measured on claims the current ranking chose to show, so "
            "it can confirm the ranking but cannot challenge it."
        )

    baseline_weights = dict(config.PRIORITY_WEIGHTS)
    if not samples:
        return FitResult(
            weights=baseline_weights,
            weights_version=config.WEIGHTS_VERSION,
            samples=0,
            exploration_samples=0,
            recommendation="keep",
            warnings=warnings + ["No analyst decisions recorded yet."],
        )

    raw, _bias = fit_logistic(samples, l2=l2)
    candidate_weights = _normalize_weights(raw)

    holdout = exploration or samples
    if not exploration:
        warnings.append(
            "No exploration labels at all, so evaluation fell back to the "
            "score-ranked sample. Treat the comparison as indicative only."
        )

    baseline = evaluate(baseline_weights, holdout, k=k)
    candidate = evaluate(candidate_weights, holdout, k=k)

    recommendation = "keep"
    b_auc, c_auc = baseline.get("auc"), candidate.get("auc")
    if not exploration or len(samples) < MIN_LABELS:
        recommendation = "collect_more"
    elif b_auc is not None and c_auc is not None and c_auc > b_auc + 0.02:
        recommendation = "ship_candidate"

    version = config.WEIGHTS_VERSION
    if recommendation == "ship_candidate":
        version = f"fitted-{version_suffix or len(samples)}"

    return FitResult(
        weights=candidate_weights,
        weights_version=version,
        samples=len(samples),
        exploration_samples=len(exploration),
        baseline=baseline,
        candidate=candidate,
        recommendation=recommendation,
        warnings=warnings,
    )
