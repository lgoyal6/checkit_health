"""Narrative layer: group posts that carry the same rumor, and track it over time.

The unit of analysis in Checkit is the *narrative*, not the post. A narrative is
one rumor ("the MMR vaccine causes autism") that shows up across many posts,
platforms, and days. Posts are ephemeral and interchangeable; the narrative is
the thing an analyst tracks, gathers evidence against, and eventually responds
to.

This module owns three things:

1. Embedding a claim into a vector (Gemini when configured, a deterministic
   hashed fallback otherwise, always ``config.EMBEDDING_DIMENSIONS`` wide and
   L2-normalized so cosine similarity is a plain dot product).
2. Assigning a claim to an existing narrative or starting a new one, by nearest
   centroid above a calibrated cosine threshold.
3. Deriving growth and lifecycle state from engagement snapshots, which is what
   makes "detect early" a measurable claim rather than an assertion.

The predecessor to this module hashed the sorted set of stemmed words in a
claim, so two claims grouped only if their word sets matched exactly. On the
bundled 34-claim corpus that produced 33 distinct clusters. See
``scripts/calibrate_threshold.py`` for the measurement that replaced it.
"""

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config

def default_threshold() -> float:
    """Calibrated cosine threshold for the active embedding path.

    Resolved at call time rather than import time so a test or a deployment can
    switch embedding modes without reloading the module.
    """
    return config.narrative_threshold()


def ambiguous_band() -> tuple:
    """Similarity range where a cheap LLM adjudication pass would earn its keep.

    Outside this band the embedding is decisive on its own, so paying for a
    model call there buys nothing.
    """
    threshold = default_threshold()
    return (threshold - 0.05, threshold + 0.10)

_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
    "was", "were", "will", "can", "does", "do", "not", "no", "if", "you",
}


# --- embedding -------------------------------------------------------------


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _features(text: str) -> List[str]:
    """Word unigrams, word bigrams, and character 4-grams.

    Character n-grams are what let the offline fallback see through
    morphological variation ("cures" / "cure", "vaccine" / "vaccines"), which
    a pure word-set signature cannot do. Bigrams keep a little word order.
    """
    words = [w for w in _words(text) if w not in _STOP]
    features = list(words)
    features += [f"{a}_{b}" for a, b in zip(words, words[1:])]
    joined = " ".join(words)
    features += [joined[i:i + 4] for i in range(max(0, len(joined) - 3))]
    return features


def _hashed_vector(text: str, dimensions: int) -> List[float]:
    """Signed feature hashing into a fixed-width, L2-normalized vector.

    Deterministic and dependency-free, so clustering works with no API key and
    tests do not need network access. Weaker than a real sentence embedding:
    it captures surface similarity, not paraphrase.
    """
    vec = [0.0] * dimensions
    for feature in _features(text):
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vec[index] += 1.0 if digest[4] % 2 else -1.0
    return _normalize(vec)


def _normalize(vec: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _gemini_embeddings(texts: List[str]) -> Optional[List[List[float]]]:
    """Managed embeddings when a key is configured, else None to fall back."""
    if not config.GOOGLE_API_KEY or not texts:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GOOGLE_API_KEY)
        response = client.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type="CLUSTERING",
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        vectors = [list(item.values) for item in response.embeddings]
        if len(vectors) != len(texts):
            return None
        return [_normalize(v) for v in vectors]
    except Exception:
        return None


def embed(texts: Iterable[str]) -> List[List[float]]:
    """Embed claim texts. Never raises; degrades to the hashed fallback."""
    items = [t or "" for t in texts]
    if not items:
        return []
    managed = _gemini_embeddings(items)
    if managed is not None:
        return managed
    return [_hashed_vector(t, config.EMBEDDING_DIMENSIONS) for t in items]


def embedding_mode() -> str:
    """Which embedding path is active, so the UI can disclose it."""
    return "managed" if config.GOOGLE_API_KEY else "hashed_fallback"


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity. Inputs from embed() are already normalized."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    return max(-1.0, min(1.0, dot))


# --- assignment ------------------------------------------------------------


@dataclass
class Candidate:
    """An existing narrative we might merge a new claim into."""
    narrative_id: str
    centroid: List[float]
    member_count: int = 1


@dataclass
class Assignment:
    narrative_id: Optional[str]
    similarity: float
    is_new: bool
    ambiguous: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "narrative_id": self.narrative_id,
            "similarity": round(self.similarity, 4),
            "is_new": self.is_new,
            "ambiguous": self.ambiguous,
        }


def assign(
    embedding: Sequence[float],
    candidates: Sequence[Candidate],
    threshold: Optional[float] = None,
) -> Assignment:
    """Nearest-centroid assignment: join the closest narrative above threshold.

    Single pass and online, so ingestion assigns as it writes and there is no
    batch reclustering job to schedule or keep correct.
    """
    threshold = default_threshold() if threshold is None else threshold
    low, high = ambiguous_band()
    best: Optional[Candidate] = None
    best_score = -1.0
    for candidate in candidates:
        score = cosine(embedding, candidate.centroid)
        if score > best_score:
            best, best_score = candidate, score
    if best is None or best_score < threshold:
        return Assignment(
            narrative_id=None,
            similarity=max(0.0, best_score) if best else 0.0,
            is_new=True,
            ambiguous=bool(best) and low <= best_score < threshold,
        )
    return Assignment(
        narrative_id=best.narrative_id,
        similarity=best_score,
        is_new=False,
        ambiguous=best_score < high,
    )


def merge_centroid(
    centroid: Sequence[float], member_count: int, embedding: Sequence[float]
) -> List[float]:
    """Running mean of member embeddings, renormalized.

    Keeping the centroid as the mean rather than the first member stops a
    narrative from drifting toward whichever post happened to arrive first.
    """
    if not centroid:
        return list(embedding)
    n = max(1, member_count)
    blended = [(c * n + e) / (n + 1) for c, e in zip(centroid, embedding)]
    return _normalize(blended)


def narrative_id_for(text: str) -> str:
    """Stable id for a brand-new narrative, seeded by its first claim."""
    return hashlib.sha256(f"narrative:{text.strip().lower()}".encode()).hexdigest()[:16]


def label_for(claims: Sequence[str]) -> str:
    """A short human-readable name for a narrative.

    Deterministic and free: the shortest claim that still contains the
    narrative's most common content words is usually the cleanest phrasing of
    the rumor. No model call, so labeling never fails or costs anything.
    """
    texts = [c.strip() for c in claims if c and c.strip()]
    if not texts:
        return "Unlabeled narrative"
    counts: Dict[str, int] = {}
    for text in texts:
        for word in set(_words(text)):
            if word not in _STOP and len(word) > 2:
                counts[word] = counts.get(word, 0) + 1
    # Only words shared by most members define the rumor. Words that appear in
    # a single post are incidental detail, and counting them would reward the
    # most verbose phrasing instead of the clearest one.
    quorum = max(1, (len(texts) + 1) // 2)
    core = {word for word, count in counts.items() if count >= quorum}
    if not core:
        return _truncate(min(texts, key=len))
    scored = sorted(texts, key=lambda t: (-len(core & set(_words(t))), len(t)))
    return _truncate(scored[0])


def _truncate(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


# --- growth and lifecycle --------------------------------------------------


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Trajectory:
    """Reach over time for one narrative, derived from engagement snapshots."""
    reach: int = 0
    previous_reach: int = 0
    growth_per_hour: float = 0.0
    prior_growth_per_hour: float = 0.0
    acceleration: float = 0.0
    observations: int = 0
    span_hours: float = 0.0
    points: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "reach": self.reach,
            "previous_reach": self.previous_reach,
            "growth_per_hour": round(self.growth_per_hour, 2),
            "prior_growth_per_hour": round(self.prior_growth_per_hour, 2),
            "acceleration": round(self.acceleration, 3),
            "observations": self.observations,
            "span_hours": round(self.span_hours, 2),
            "points": self.points,
        }


def trajectory(snapshots: Sequence[Dict[str, Any]]) -> Trajectory:
    """Collapse per-post engagement snapshots into a narrative reach curve.

    Each snapshot is {post_id, observed_at, like_count, retweet_count}. Posts
    are observed on the ingest schedule, so different posts have different
    observation times; we bucket by observation timestamp and carry each post's
    last known value forward, which is what makes the total comparable across
    buckets.
    """
    rows = []
    for snap in snapshots:
        observed = _parse_ts(snap.get("observed_at"))
        if observed is None:
            continue
        reach = int(snap.get("like_count") or 0) + int(snap.get("retweet_count") or 0)
        rows.append((observed, str(snap.get("post_id") or ""), reach))
    if not rows:
        return Trajectory()

    rows.sort(key=lambda r: r[0])
    latest_per_post: Dict[str, int] = {}
    points: List[Dict[str, Any]] = []
    for observed, post_id, reach in rows:
        latest_per_post[post_id] = reach
        total = sum(latest_per_post.values())
        stamp = observed.isoformat()
        if points and points[-1]["at"] == stamp:
            points[-1]["reach"] = total
        else:
            points.append({"at": stamp, "reach": total})

    traj = Trajectory(
        reach=points[-1]["reach"],
        previous_reach=points[-2]["reach"] if len(points) > 1 else 0,
        observations=len(points),
    )
    first_at = _parse_ts(points[0]["at"])
    last_at = _parse_ts(points[-1]["at"])
    if first_at and last_at:
        traj.span_hours = max(0.0, (last_at - first_at).total_seconds() / 3600)
    traj.points = points

    traj.growth_per_hour = _rate(points[-2], points[-1]) if len(points) > 1 else 0.0
    traj.prior_growth_per_hour = _rate(points[-3], points[-2]) if len(points) > 2 else 0.0
    if traj.prior_growth_per_hour > 0:
        traj.acceleration = traj.growth_per_hour / traj.prior_growth_per_hour
    elif traj.growth_per_hour > 0:
        traj.acceleration = float("inf")
    return traj


def _rate(earlier: Dict[str, Any], later: Dict[str, Any]) -> float:
    start, end = _parse_ts(earlier["at"]), _parse_ts(later["at"])
    if not start or not end:
        return 0.0
    hours = (end - start).total_seconds() / 3600
    if hours <= 0:
        return 0.0
    return max(0.0, (later["reach"] - earlier["reach"]) / hours)


# Lifecycle drives which response mode is even offered, so the boundaries are
# named constants rather than magic numbers buried in the branches.
EMERGING_MAX_AGE_HOURS = 24.0
DORMANT_AFTER_HOURS = 72.0
ACCELERATING_RATIO = 1.2
DECLINING_RATIO = 0.5
# Growth below this is noise, not a trend. Without a floor, a narrative that
# picked up five likes overnight has an infinite acceleration ratio (its prior
# rate was zero) and would be reported as accelerating alongside a genuine
# spike, which makes the signal useless for triage.
MIN_MEANINGFUL_GROWTH_PER_HOUR = 5.0


def lifecycle_state(
    traj: Trajectory,
    first_seen_at: Any = None,
    last_seen_at: Any = None,
    now: Optional[datetime] = None,
) -> str:
    """Where a narrative sits on its curve.

    Returns one of: emerging, accelerating, peaking, declining, dormant.
    Response mode is a function of this, because a rumor caught while emerging
    can be pre-bunked, and one that is already declining should usually be left
    alone rather than revived.
    """
    now = now or datetime.now(timezone.utc)
    last_seen = _parse_ts(last_seen_at)
    if last_seen and (now - last_seen).total_seconds() / 3600 > DORMANT_AFTER_HOURS:
        return "dormant"

    if traj.observations < 2:
        first_seen = _parse_ts(first_seen_at)
        if first_seen and (now - first_seen).total_seconds() / 3600 <= EMERGING_MAX_AGE_HOURS:
            return "emerging"
        return "watching"

    if traj.growth_per_hour <= 0:
        return "declining"
    if traj.growth_per_hour < MIN_MEANINGFUL_GROWTH_PER_HOUR:
        return "watching"

    first_seen = _parse_ts(first_seen_at)
    young = (
        first_seen is not None
        and (now - first_seen).total_seconds() / 3600 <= EMERGING_MAX_AGE_HOURS
    )
    if traj.acceleration >= ACCELERATING_RATIO:
        return "emerging" if young else "accelerating"
    if traj.acceleration <= DECLINING_RATIO:
        return "declining"
    return "peaking"


# --- threshold calibration -------------------------------------------------


# Merging two different rumors is worse than failing to merge two phrasings of
# the same one: a wrong merge attaches evidence about seizures to a claim about
# autism and corrupts the ledger, while a missed merge only means an analyst
# tracks two narratives instead of one. So threshold selection optimizes
# F-beta with beta < 1, which weights precision above recall.
PRECISION_BETA = 0.5


def evaluate_threshold(
    pairs: Sequence[Tuple[Sequence[float], Sequence[float], bool]],
    threshold: float,
    beta: float = PRECISION_BETA,
) -> Dict[str, float]:
    """Pairwise precision/recall/F1/F-beta for one threshold on labeled pairs."""
    tp = fp = fn = tn = 0
    for left, right, same in pairs:
        predicted = cosine(left, right) >= threshold
        if predicted and same:
            tp += 1
        elif predicted and not same:
            fp += 1
        elif not predicted and same:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    b2 = beta * beta
    denom = (b2 * precision) + recall
    fbeta = (1 + b2) * precision * recall / denom if denom else 0.0
    return {
        "threshold": round(threshold, 3),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fbeta": round(fbeta, 4),
        "beta": beta,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
    }


def separation(
    pairs: Sequence[Tuple[Sequence[float], Sequence[float], bool]],
) -> Dict[str, Any]:
    """How distinguishable same-rumor pairs are from different-rumor pairs.

    Reported as AUC over the similarity score plus the overlap between the two
    distributions. An AUC near 0.5 means the embedding carries no usable signal
    for this task and no threshold will rescue it, which is a result worth
    surfacing loudly rather than burying under a best-effort number.
    """
    positives = sorted(cosine(a, b) for a, b, same in pairs if same)
    negatives = sorted(cosine(a, b) for a, b, same in pairs if not same)
    if not positives or not negatives:
        return {"auc": None, "verdict": "insufficient labels"}
    wins = ties = 0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    auc = (wins + 0.5 * ties) / (len(positives) * len(negatives))
    overlap_low = max(min(positives), min(negatives))
    overlap_high = min(max(positives), max(negatives))
    if auc >= 0.90:
        verdict = "clean separation"
    elif auc >= 0.75:
        verdict = "usable with a precision-weighted threshold"
    else:
        verdict = "unusable for semantic clustering"
    return {
        "auc": round(auc, 4),
        "verdict": verdict,
        "positive_median": round(positives[len(positives) // 2], 4),
        "negative_median": round(negatives[len(negatives) // 2], 4),
        "overlap_range": [round(overlap_low, 4), round(overlap_high, 4)],
    }


def sweep_threshold(
    pairs: Sequence[Tuple[Sequence[float], Sequence[float], bool]],
    start: float = 0.20,
    stop: float = 0.95,
    step: float = 0.01,
    beta: float = PRECISION_BETA,
    min_recall: float = 0.20,
) -> Dict[str, Any]:
    """Sweep cosine thresholds and return the F1-maximizing one.

    This is the measurement that should set DEFAULT_THRESHOLD. Guessing the
    threshold is how you end up with a clustering feature that silently does
    nothing.
    """
    results = []
    value = start
    while value <= stop + 1e-9:
        results.append(evaluate_threshold(pairs, value, beta=beta))
        value += step
    if not results:
        return {"best": None, "results": [], "separation": separation(pairs)}
    # Only consider thresholds that actually recall something; a threshold that
    # merges nothing has perfect precision and is useless.
    usable = [r for r in results if r["recall"] >= min_recall] or results
    best = max(usable, key=lambda r: (r["fbeta"], r["precision"]))
    return {
        "best": best,
        "results": results,
        "pairs": len(pairs),
        "separation": separation(pairs),
    }


# --- ambiguous-pair adjudication -------------------------------------------

ADJUDICATION_PROMPT = """You decide whether two social-media statements assert \
the SAME specific, checkable health claim, so a misinformation analyst can \
track them as one rumor.

Same means: same subject, same asserted effect, same direction. Wording, \
tense, hedging, and extra detail do not matter.

Different means: different subject, different outcome, opposite direction, or \
one is a claim and the other is a correction of it. Sharing vocabulary is not \
enough. "MMR causes autism" and "MMR causes seizures" are DIFFERENT. "COVID \
shots cause myocarditis" and "COVID infection causes myocarditis" are \
DIFFERENT.

Respond with only a JSON object: {"same": true|false, "reason": "<12 words"}
"""


def adjudicate_pair(client: Any, left: str, right: str) -> Optional[bool]:
    """Ask a model whether two claims are the same rumor. None if unavailable.

    Only worth calling inside ``ambiguous_band()``: outside it the embedding is
    already decisive, so a model call buys nothing and costs a request. Returns
    None on any failure so a model outage degrades to the embedding's answer
    rather than dropping the claim.
    """
    try:
        from google.genai import types

        from classifier import _parse_json
        from rate_limiter import gemini_limiter

        gemini_limiter.acquire()
        response = client.models.generate_content(
            model=config.MODEL_NAME,
            contents=f"Statement A: {left}\nStatement B: {right}",
            config=types.GenerateContentConfig(
                system_instruction=ADJUDICATION_PROMPT,
                response_mime_type="application/json",
                max_output_tokens=100,
            ),
        )
        parsed = _parse_json((response.text or "").strip())
        value = parsed.get("same")
        return bool(value) if isinstance(value, bool) else None
    except Exception:
        return None
