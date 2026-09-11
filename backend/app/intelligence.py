"""Transparent, deterministic risk signals layered around model output.

These helpers deliberately do not decide whether a claim is true.  They make
the triage system more useful to analysts by exposing clustering, urgency,
evidence quality, adverse-event routing, and governance metadata.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import config


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
    """Deprecated lexical signature; kept only as an offline fallback id.

    This groups two claims only when their normalized word *sets* match
    exactly, so "Ivermectin cures COVID" and "Ivermectin is a cure for
    COVID-19" land in different buckets. Measured on the bundled corpus it
    produced 33 distinct clusters from 34 claims. Real grouping lives in
    ``narratives.py``; this remains so a row with no narrative assignment
    still has a stable identifier.
    """
    normalized = normalize_claim(text)
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
    if reach >= config.ESCALATION_REACH:
        reasons.append("high reach")
        severity = "high" if severity == "routine" else severity
    if confidence is not None and confidence < config.ESCALATION_CONFIDENCE:
        reasons.append("model uncertainty")
        severity = "review" if severity == "routine" else severity
    return {
        "severity": severity,
        "human_review_required": severity != "routine",
        "reasons": reasons,
        "rule_version": config.ESCALATION_RULE_VERSION,
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
    weights: Optional[Dict[str, float]] = None,
    weights_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Explainable 0-100 review priority, never a truth or harm verdict.

    Components are returned alongside the score and persisted with the
    ``weights_version`` that produced them, so a later retune against real
    analyst decisions can be evaluated against this one instead of quietly
    replacing it.
    """
    weights = weights or config.PRIORITY_WEIGHTS
    uncertainty = 1.0 - (confidence if confidence is not None else 0.5)
    reach_signal = min(1.0, max(0, reach) / max(1, config.REACH_SATURATION))
    harm_signal = 1.0 if adverse_event and adverse_event.get("detected") else 0.25
    components = {
        "reach": round(reach_signal, 3),
        "potential_harm": round(harm_signal, 3),
        "uncertainty": round(uncertainty, 3),
    }
    raw = sum(weights.get(name, 0.0) * value for name, value in components.items())
    total = sum(weights.values()) or 1.0
    return {
        "score": round(100 * raw / total),
        "components": components,
        "weights": dict(weights),
        "weights_version": weights_version or config.WEIGHTS_VERSION,
        "meaning": "review priority, not likelihood the claim is false",
    }


def assess_claim(
    text: str,
    confidence: Optional[float],
    fact_check_source: Optional[str] = None,
    fact_check_url: Optional[str] = None,
    reach: int = 0,
    narrative: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    adverse = adverse_event_routing(text)
    evidence = evidence_quality(fact_check_source, fact_check_url)
    response = (
        "Open and verify the matched source before communicating a conclusion."
        if evidence["score"] >= 2
        else "Queue evidence retrieval; do not treat the missing match as proof."
    )
    narrative = narrative or {}
    return {
        "normalized_claim": normalize_claim(text),
        "cluster_id": cluster_id(text),
        "narrative": {
            "narrative_id": narrative.get("narrative_id"),
            "label": narrative.get("label"),
            "member_count": narrative.get("member_count"),
            "lifecycle_state": narrative.get("lifecycle_state"),
            "similarity": narrative.get("similarity"),
        },
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
            "status": (
                "recurring"
                if (narrative.get("member_count") or 0) > 1
                else "first_observation"
            ),
            "instruction": "Open the narrative to see every post carrying this claim.",
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
    """Attach the signals the monitor ranks and renders by.

    Narrative assignment happens once at write time and is read back off the
    row, so the monitor shows the same grouping the stored record has rather
    than recomputing a different one per request.
    """
    enriched = []
    for original in rows:
        row = dict(original)
        row.setdefault(
            "cluster_id", cluster_id(row.get("claim") or row.get("text") or "")
        )
        reach = int(row.get("like_count") or 0) + int(row.get("retweet_count") or 0)
        row["velocity"] = velocity(
            row.get("like_count") or 0,
            row.get("retweet_count") or 0,
            row.get("timestamp"),
        )
        row["priority"] = priority_score(
            row.get("confidence"),
            reach,
            adverse_event_routing(row.get("claim") or row.get("text") or ""),
        )
        enriched.append(row)
    return enriched
