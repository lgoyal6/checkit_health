"""Stage 2: LLM classification with Google Gemini.

Each post is sent to Gemini Flash for triage. We use JSON response mode so
parsing stays robust. Posts are processed in small batches, with a short
sleep between batches to respect the 15 req/min free-tier limit.
"""

import json
import time
from pathlib import Path
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

Always include:
- reasoning: one short sentence explaining why you chose this label

A MEDICAL_CLAIM must assert something specific that could be checked against
evidence. Angry opinions, insults, political rants, and calls to action are
NOT claims — even when they mention a drug company, vaccine, or health topic.
The emotional intensity of a post says nothing about whether it contains a
checkable claim.

Examples:

Post: "Ivermectin cures COVID-19 in 48 hours, doctors are hiding it"
{"label": "MEDICAL_CLAIM", "claim": "Ivermectin cures COVID-19 within 48 hours.", "topic": "drug", "confidence": 0.95, "reasoning": "Specific, testable assertion that a drug cures a disease in a set time."}

Post: "Drinking celery juice every morning reversed my mother's arthritis completely"
{"label": "MEDICAL_CLAIM", "claim": "Drinking celery juice daily reverses arthritis.", "topic": "nutrition", "confidence": 0.82, "reasoning": "Concrete cause-and-effect health claim that can be checked against evidence."}

Post: "EVERYONE AT PFIZER DESERVES THE DEATH PENALTY FOR WHAT THEY DID"
{"label": "NOISE", "claim": null, "topic": null, "confidence": null, "reasoning": "An angry opinion with no checkable factual claim."}

Post: "wake up sheeple!! big pharma doesn't want you to be healthy, do your own research"
{"label": "NOISE", "claim": null, "topic": null, "confidence": null, "reasoning": "A vague slogan and call to action, not a specific claim."}

Post: "Feeling grateful today, got my flu shot at the pharmacy and the staff were lovely"
{"label": "GENERAL_HEALTH", "claim": null, "topic": null, "confidence": null, "reasoning": "A personal experience about a health topic, not a factual assertion."}

Respond ONLY with a single JSON object. No prose, no markdown fences.
Schema:
{"label": "MEDICAL_CLAIM" | "GENERAL_HEALTH" | "NOISE",
 "claim": string | null,
 "topic": "vaccine" | "drug" | "treatment" | "nutrition" | "other" | null,
 "confidence": number | null,
 "reasoning": string}
"""


FALSIFIABILITY_PROMPT = """You are a fact-checking triage assistant. Given a single health-related claim, decide whether it is FALSIFIABLE: could it, in principle, be checked against scientific or medical evidence and shown to be true or false?

Falsifiable (answer YES): specific empirical assertions, e.g. "Vitamin C prevents the common cold", "The MMR vaccine causes autism", "Ivermectin cures COVID-19".

Not falsifiable (answer NO): pure opinion, insults, vague slogans, value judgments, or calls to action, e.g. "Big pharma is evil", "Do your own research", "Doctors can't be trusted".

Respond ONLY with a single JSON object, no prose:
{"falsifiable": true | false}
"""


def classify_posts(
    posts: List[Dict[str, Any]],
    client: Optional[Any] = None,
    checkpoint_path: Optional[str] = None,
    on_progress: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Classify each post. Returns the input list with classification fields added.

    If `checkpoint_path` is provided:
      - Previously-classified posts (matched by id) are loaded and skipped.
      - Results are flushed to disk after every post so a Ctrl+C is recoverable.
    """
    if client is None:
        from google import genai
        client = genai.Client(api_key=config.GOOGLE_API_KEY)

    done: Dict[str, Dict[str, Any]] = {}
    if checkpoint_path and Path(checkpoint_path).exists():
        with Path(checkpoint_path).open("r", encoding="utf-8") as f:
            for row in json.load(f):
                done[row["id"]] = row

    results: List[Dict[str, Any]] = []
    for i, post in enumerate(posts):
        if post["id"] in done:
            results.append(done[post["id"]])
            if on_progress:
                on_progress(i + 1, len(posts), results[-1], skipped=True)
            continue

        classification = _classify_one(client, post["text"])
        row = {**post, "classification": classification}
        results.append(row)

        if checkpoint_path:
            _atomic_write_json(results, checkpoint_path)
        if on_progress:
            on_progress(i + 1, len(posts), row, skipped=False)

        if i < len(posts) - 1 and config.BATCH_DELAY_SECONDS > 0:
            time.sleep(config.BATCH_DELAY_SECONDS)
    return results


def _atomic_write_json(data: List[Dict[str, Any]], path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(p)


def _classify_one(client: Any, text: str) -> Dict[str, Any]:
    from google.genai import types
    backoff = config.RETRY_INITIAL_BACKOFF_SECONDS
    last_err: Optional[str] = None
    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=config.MODEL_NAME,
                contents=f"Post:\n{text}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    max_output_tokens=config.MAX_TOKENS,
                ),
            )
            raw = (response.text or "").strip()
            parsed = _parse_json(raw)
            return _normalize(parsed)
        except Exception as e:
            last_err = str(e)
            is_rate_limit = "429" in last_err or "RESOURCE_EXHAUSTED" in last_err
            if is_rate_limit and attempt < config.RETRY_MAX_ATTEMPTS - 1:
                print(f"  [rate-limited, sleeping {backoff}s then retry {attempt + 2}/{config.RETRY_MAX_ATTEMPTS}]")
                time.sleep(backoff)
                backoff *= 2
                continue
            return {
                "label": "NOISE",
                "claim": None,
                "topic": None,
                "confidence": None,
                "error": last_err,
            }
    return {
        "label": "NOISE",
        "claim": None,
        "topic": None,
        "confidence": None,
        "error": last_err,
    }


def _parse_json(raw: str) -> Dict[str, Any]:
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
        "reasoning": parsed.get("reasoning"),
    }


def is_falsifiable(claim: str, client: Optional[Any] = None) -> bool:
    """Second-pass check: is this claim actually checkable against evidence?

    Runs one extra LLM call. Returns True on any error so we fail open — a
    borderline claim reaching storage is cheaper than silently dropping a
    real one. `--dry-run` never reaches this code, so no API calls happen there.
    """
    if not claim or not claim.strip():
        return False
    if client is None:
        from google import genai
        client = genai.Client(api_key=config.GOOGLE_API_KEY)

    from google.genai import types
    try:
        response = client.models.generate_content(
            model=config.MODEL_NAME,
            contents=f"Claim:\n{claim}",
            config=types.GenerateContentConfig(
                system_instruction=FALSIFIABILITY_PROMPT,
                response_mime_type="application/json",
                max_output_tokens=64,
            ),
        )
        parsed = _parse_json((response.text or "").strip())
        return bool(parsed.get("falsifiable"))
    except Exception:
        return True


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
