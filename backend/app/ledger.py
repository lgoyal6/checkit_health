"""One entry point the pipeline and the API both call after building records.

Everything that has to happen for a batch of claims to become part of the
narrative ledger happens here, in one place and in one order, so the CLI and
the web path cannot drift apart:

1. assign each claim to a narrative (creating narratives as needed)
2. write the narrative id and embedding back onto the claims rows
3. append an engagement snapshot, which is what makes growth measurable
4. append the triage score that was in force, which is the feedback dataset
5. recompute lifecycle state for every narrative the batch touched

Steps 3 and 4 are append-only. The claims table upserts on ``post_id``, so
without them a re-ingest silently destroys the previous engagement reading and
there is no record of what the system thought at the moment it surfaced a
claim.
"""

import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import config
import narrative_store as ns
from intelligence import adverse_event_routing, escalation, priority_score


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def score_records(
    records: Sequence[Dict[str, Any]],
    exploration_rate: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Compute the triage score for each record and mark the exploration slot.

    A fraction of the batch is flagged ``selected_by='exploration'``. Those
    claims are surfaced to analysts regardless of where they ranked, and they
    are the only unbiased sample of analyst judgment the system ever collects:
    labels gathered from a score-ordered queue can only confirm the ordering
    that produced them. Weight fitting is evaluated on this slice.
    """
    rate = config.EXPLORATION_RATE if exploration_rate is None else exploration_rate
    rng = rng or random.Random()
    entries = []
    for record in records:
        text = record.get("claim") or record.get("text") or ""
        reach = int(record.get("like_count") or 0) + int(record.get("retweet_count") or 0)
        adverse = adverse_event_routing(text)
        priority = priority_score(record.get("confidence"), reach, adverse)
        rules = escalation(record.get("confidence"), reach, adverse)
        entries.append({
            "post_id": record.get("post_id"),
            "narrative_id": record.get("narrative_id"),
            "scored_at": _now(),
            "score": priority["score"],
            "components": priority["components"],
            "weights_version": priority["weights_version"],
            "rule_version": rules["rule_version"],
            "model_name": config.MODEL_NAME,
            "selected_by": "exploration" if rng.random() < rate else "rank",
        })
    return entries


def ingest(
    records: List[Dict[str, Any]],
    db_path: Optional[str] = None,
    observed_at: Optional[str] = None,
    exploration_rate: Optional[float] = None,
    rng: Optional[random.Random] = None,
    adjudicate: bool = True,
) -> Dict[str, Any]:
    """Fold a batch of freshly built claim records into the narrative ledger.

    Call this *after* the records are already written to the claims table, so
    the narrative id can be attached to rows that exist.
    """
    if not records:
        return {"claims": 0, "narratives": 0, "snapshots": 0, "scores": 0}

    observed_at = observed_at or _now()
    ns.assign_narratives(records, db_path=db_path, adjudicate=adjudicate)
    ns.persist_claim_narratives(records, db_path=db_path)
    snapshots = ns.record_snapshots(records, observed_at=observed_at, db_path=db_path)
    entries = score_records(records, exploration_rate=exploration_rate, rng=rng)
    scores = ns.record_scores(entries, db_path=db_path)

    touched = sorted({r["narrative_id"] for r in records if r.get("narrative_id")})
    states: Dict[str, str] = {}
    for narrative_id in touched:
        result = ns.refresh_lifecycle(narrative_id, db_path=db_path)
        if result:
            states[narrative_id] = result["lifecycle_state"]

    return {
        "claims": len(records),
        "narratives": len(touched),
        "snapshots": snapshots,
        "scores": scores,
        "exploration_selected": sum(
            1 for e in entries if e["selected_by"] == "exploration"
        ),
        "lifecycle_states": states,
        "clustering": ns.clustering_quality(),
    }
