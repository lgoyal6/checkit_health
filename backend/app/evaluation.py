"""Offline evaluation metrics for retrieval and calibrated conclusions."""

from typing import Any, Dict, Iterable, List


def retrieval_metrics(cases: Iterable[Dict[str, Any]], results: Iterable[List[Dict[str, Any]]]) -> Dict[str, float]:
    cases, results = list(cases), list(results)
    hits, reciprocal_ranks = 0, 0.0
    for case, rows in zip(cases, results):
        wanted = set(case.get("relevant_source_types") or [])
        rank = next(
            (i for i, row in enumerate(rows, 1) if row.get("source_type") in wanted),
            None,
        )
        if rank:
            hits += 1
            reciprocal_ranks += 1 / rank
    total = len(cases) or 1
    return {
        "recall_at_k": round(hits / total, 4),
        "mean_reciprocal_rank": round(reciprocal_ranks / total, 4),
    }


def conclusion_accuracy(expected: Iterable[str], predicted: Iterable[str]) -> float:
    pairs = list(zip(expected, predicted))
    return round(sum(left == right for left, right in pairs) / (len(pairs) or 1), 4)
