"""Transparent, deterministic risk signals layered around model output.

These helpers deliberately do not decide whether a claim is true.  They make
the triage system more useful to analysts by exposing clustering, urgency,
evidence quality, adverse-event routing, and governance metadata.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


ADVERSE_EVENT_TERMS = {
    "adverse reaction", "allergic reaction", "anaphylaxis", "bleeding",
    "couldn't breathe", "could not breathe", "difficulty breathing",
    "hospitalized", "overdose", "poisoning", "seizure", "side effect",
    "suicidal", "unconscious",
}
URGENT_TERMS = {
    "anaphylaxis", "couldn't breathe", "could not breathe",
    "difficulty breathing", "overdose", "poisoning", "seizure",
    "suicidal", "unconscious",
}
AUTHORITATIVE_PUBLISHERS = {
    "cdc", "fda", "nih", "nhs", "who", "world health organization",
    "cochrane", "mayo clinic",
}
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}


def normalize_claim(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    normalized = []
    for word in words:
        if word in STOP_WORDS:
            continue
        if word.endswith("ied") and len(word) > 4:
            word = f"{word[:-3]}y"
        elif word.endswith("ed") and len(word) > 4:
            word = word[:-2]
        elif word.endswith("es") and len(word) > 4:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        normalized.append(word)
    return " ".join(normalized)


def cluster_id(text: str) -> str:
    normalized = normalize_claim(text)
    # Sorting makes small wording changes more likely to share a cluster.
    signature = " ".join(sorted(set(normalized.split())))
    return hashlib.sha256(signature.encode()).hexdigest()[:12]


def detect_language(text: str) -> Dict[str, Any]:
    """Return a conservative language signal without pretending translation."""
    if re.search(r"[\u4e00-\u9fff]", text):
        return {"code": "zh", "name": "Chinese", "translation_applied": False}
    if re.search(r"[\u0600-\u06ff]", text):
        return {"code": "ar", "name": "Arabic", "translation_applied": False}
    if re.search(r"[\u0400-\u04ff]", text):
        return {"code": "ru", "name": "Russian", "translation_applied": False}
    if re.search(r"[áéíóúñ¿¡]", text.lower()):
        return {"code": "es", "name": "Spanish", "translation_applied": False}
    return {"code": "en", "name": "English", "translation_applied": False}


def evidence_quality(
    fact_check_source: Optional[str], fact_check_url: Optional[str]
) -> Dict[str, Any]:
    source = (fact_check_source or "").lower()
    if any(publisher in source for publisher in AUTHORITATIVE_PUBLISHERS):
        level, score = "high", 4
        reason = "Matched an authoritative public-health or evidence-review source."
    elif fact_check_source and fact_check_url:
        level, score = "moderate", 3
        reason = "Matched a named published fact-check with a review link."
    elif fact_check_source:
        level, score = "limited", 2
        reason = "A named source was found, but no review link was available."
    else:
        level, score = "unreviewed", 0
        reason = "No published evidence match was retrieved."
    return {
        "level": level,
        "score": score,
        "scale": "0-4",
        "reason": reason,
        "hierarchy": [
            "systematic review or clinical guideline",
            "peer-reviewed clinical study",
            "authoritative public-health guidance",
            "published fact-check",
            "unreviewed",
        ],
    }


def adverse_event_routing(text: str) -> Dict[str, Any]:
    lowered = text.lower()
    matches = sorted(term for term in ADVERSE_EVENT_TERMS if term in lowered)
    urgent = any(term in lowered for term in URGENT_TERMS)
    if urgent:
        route = "urgent_human_review"
    elif matches:
        route = "adverse_event_review"
    else:
        route = "standard_claim_review"
    return {
        "detected": bool(matches),
        "matched_terms": matches,
        "route": route,
        "notice": (
            "This is a routing signal, not a diagnosis or emergency assessment."
            if matches else None
        ),
    }


def escalation(
    confidence: Optional[float],
    reach: int = 0,
    adverse_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    reasons = []
    severity = "routine"
    if adverse_event and adverse_event.get("detected"):
        reasons.append("possible adverse-event report")
        severity = "high"
    if adverse_event and adverse_event.get("route") == "urgent_human_review":
        reasons.append("urgent symptom language")
        severity = "critical"
    if reach >= 10_000:
        reasons.append("high reach")
        severity = "high" if severity == "routine" else severity
    if confidence is not None and confidence < 0.8:
        reasons.append("model uncertainty")
        severity = "review" if severity == "routine" else severity
    return {
        "severity": severity,
        "human_review_required": severity != "routine",
        "reasons": reasons,
        "rule_version": "2026-07-23",
    }


def velocity(
    likes: int, reposts: int, timestamp: Optional[str], now: Optional[datetime] = None
) -> Dict[str, Any]:
    reach = max(0, likes or 0) + max(0, reposts or 0)
    now = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat((timestamp or "").replace("Z", "+00:00"))
        hours = max((now - created).total_seconds() / 3600, 1.0)
        per_hour = round(reach / hours, 2)
    except (TypeError, ValueError):
        hours, per_hour = None, None
    return {"reach": reach, "age_hours": hours, "interactions_per_hour": per_hour}


def priority_score(
    confidence: Optional[float],
    reach: int,
    adverse_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Explainable 0-100 review priority, never a truth or harm verdict."""
    uncertainty = 1.0 - (confidence if confidence is not None else 0.5)
    reach_signal = min(1.0, max(0, reach) / 10_000)
    harm_signal = 1.0 if adverse_event and adverse_event.get("detected") else 0.25
    score = round(100 * (0.45 * reach_signal + 0.35 * harm_signal + 0.2 * uncertainty))
    return {
        "score": score,
        "components": {
            "reach": round(reach_signal, 3),
            "potential_harm": round(harm_signal, 3),
            "uncertainty": round(uncertainty, 3),
        },
        "meaning": "review priority, not likelihood the claim is false",
    }


def assess_claim(
    text: str,
    confidence: Optional[float],
    fact_check_source: Optional[str] = None,
    fact_check_url: Optional[str] = None,
    reach: int = 0,
) -> Dict[str, Any]:
    adverse = adverse_event_routing(text)
    evidence = evidence_quality(fact_check_source, fact_check_url)
    response = (
        "Open and verify the matched source before communicating a conclusion."
        if evidence["score"] >= 2
        else "Queue evidence retrieval; do not treat the missing match as proof."
    )
    return {
        "normalized_claim": normalize_claim(text),
        "cluster_id": cluster_id(text),
        "language": detect_language(text),
        "evidence_quality": evidence,
        "adverse_event": adverse,
        "escalation": escalation(confidence, reach, adverse),
        "priority": priority_score(confidence, reach, adverse),
        "coordination_signal": {
            "status": "not_assessed",
            "reason": "A single claim is insufficient to infer coordinated behavior.",
        },
        "repeated_claim": {
            "status": "cluster_candidate",
            "instruction": "Compare cluster_id across the monitored corpus.",
        },
        "modalities": ["text"],
        "response_guidance": response,
        "privacy": {
            "input_scope": "submitted public text",
            "account_authenticity_inference": False,
            "automated_enforcement": False,
        },
        "audit": {
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "method": "deterministic rules plus disclosed model classification",
            "final_judgment": "human",
        },
    }


def add_monitor_signals(rows: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    enriched = []
    for original in rows:
        row = dict(original)
        row["cluster_id"] = cluster_id(row.get("claim") or row.get("text") or "")
        row["velocity"] = velocity(
            row.get("like_count") or 0,
            row.get("retweet_count") or 0,
            row.get("timestamp"),
        )
        enriched.append(row)
    return enriched
