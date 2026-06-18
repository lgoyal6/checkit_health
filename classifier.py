"""Stage 2: LLM classification.

Each post is sent to Claude Haiku for triage. We ask for a single JSON object
back so parsing stays robust. Posts are processed in small batches to keep
under per-minute rate limits and to make partial failures easy to retry.
"""

import json
from typing import List, Dict, Any, Optional

import config


SYSTEM_PROMPT = """You are a medical misinformation triage classifier.

For each social media post, classify it as exactly one of:
- MEDICAL_CLAIM: makes a specific factual claim about health, medicine, treatments, drugs, vaccines, or nutrition that is checkable (true or false)
- GENERAL_HEALTH: health-related but not a checkable claim (PSAs, personal experiences, generic advice, news links)
- NOISE: not health-related at all

If MEDICAL_CLAIM, also extract:
- claim: the specific assertion in 1 sentence
- topic: one of [vaccine, drug, treatment, nutrition, other]
- confidence: 0.0-1.0, how confident you are this is a discrete factual medical claim worth fact-checking

Respond ONLY with a single JSON object. No prose, no markdown fences.
Schema:
{"label": "MEDICAL_CLAIM" | "GENERAL_HEALTH" | "NOISE",
 "claim": string | null,
 "topic": "vaccine" | "drug" | "treatment" | "nutrition" | "other" | null,
 "confidence": number | null}
"""


def classify_posts(posts: List[Dict[str, Any]], client: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Classify each post. Returns the input list with classification fields added."""
    if client is None:
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

    results: List[Dict[str, Any]] = []
    for start in range(0, len(posts), config.BATCH_SIZE):
        batch = posts[start : start + config.BATCH_SIZE]
        for post in batch:
            classification = _classify_one(client, post["text"])
            results.append({**post, "classification": classification})
    return results


def _classify_one(client: Any, text: str) -> Dict[str, Any]:
    try:
        response = client.messages.create(
            model=config.MODEL_NAME,
            max_tokens=config.MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Post:\n{text}"}],
        )
        raw = response.content[0].text.strip()
        parsed = _parse_json(raw)
        return _normalize(parsed)
    except Exception as e:
        return {
            "label": "NOISE",
            "claim": None,
            "topic": None,
            "confidence": None,
            "error": str(e),
        }


def _parse_json(raw: str) -> Dict[str, Any]:
    # Tolerate stray code fences if the model adds them despite instructions.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _normalize(parsed: Dict[str, Any]) -> Dict[str, Any]:
    label = parsed.get("label")
    if label not in config.VALID_LABELS:
        label = "NOISE"
    topic = parsed.get("topic")
    if topic is not None and topic not in config.VALID_TOPICS:
        topic = "other"
    confidence = parsed.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = None
    return {
        "label": label,
        "claim": parsed.get("claim"),
        "topic": topic,
        "confidence": confidence,
    }


def filter_for_review(classified: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only MEDICAL_CLAIM posts that meet the confidence threshold."""
    out = []
    for post in classified:
        c = post["classification"]
        if c["label"] != "MEDICAL_CLAIM":
            continue
        if c["confidence"] is None or c["confidence"] < config.CONFIDENCE_THRESHOLD:
            continue
        out.append(post)
    return out
