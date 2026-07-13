"""Stage 2.9: structured evidence report for a single claim.

Separate from classifier.py's triage call on purpose — triage runs on every
candidate post and needs to be cheap and fast, while this report is a longer,
more expensive generation that should only run when an analyst or user
explicitly asks to see the full evidence write-up for one claim (via the
/report endpoint), not on every classification.

Produces the Rumor / Confidence Level / Summary / Key Facts / Analysis /
Conclusion structure shown in the analyst UI.
"""

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import config
from classifier import _parse_json  # reuse the existing fenced-JSON parser
from rate_limiter import gemini_limiter


class ClaimReportResponse(BaseModel):
    rumor: str
    confidence_level: str  # "low" | "medium" | "high"
    summary: str
    key_facts: List[str] = Field(default_factory=list)
    analysis: str
    conclusion: str


REPORT_SYSTEM_PROMPT = """You are a careful medical-evidence explainer for a \
health-misinformation monitoring tool. Given a claim circulating on social \
media, and optionally an existing fact-check verdict, produce a structured, \
neutral report a general reader can act on.

Rules:
- Never state a stronger conclusion than the evidence supports.
- Prefer "current evidence does not show X" over "X is false" when the \
research is simply thin, and reserve strong "this is false" language for \
claims actually contradicted by solid evidence.
- confidence_level reflects how SETTLED the underlying science is, not how \
sure you are that this is a real claim.
- key_facts must be 3-6 individually verifiable, non-overlapping statements \
— not restatements of the rumor.
- rumor must be phrased as a plain yes/no question, e.g. "Do NMN and NAD+ \
supplements prolong life?"

Respond ONLY with a single JSON object, no prose, no markdown fences.
Schema:
{"rumor": string,
 "confidence_level": "low" | "medium" | "high",
 "summary": string,
 "key_facts": [string, ...],
 "analysis": string,
 "conclusion": string}
"""


def _normalize(parsed: Dict[str, Any]) -> ClaimReportResponse:
    confidence_level = parsed.get("confidence_level")
    if confidence_level not in {"low", "medium", "high"}:
        confidence_level = "low"
    key_facts = parsed.get("key_facts") or []
    if not isinstance(key_facts, list):
        key_facts = []
    key_facts = [str(f).strip() for f in key_facts if str(f).strip()]
    return ClaimReportResponse(
        rumor=str(parsed.get("rumor") or "").strip(),
        confidence_level=confidence_level,
        summary=str(parsed.get("summary") or "").strip(),
        key_facts=key_facts,
        analysis=str(parsed.get("analysis") or "").strip(),
        conclusion=str(parsed.get("conclusion") or "").strip(),
    )


def generate_claim_report(
    client: Any,
    claim_text: str,
    topic: Optional[str] = None,
    fact_check_verdict: Optional[str] = None,
    fact_check_source: Optional[str] = None,
    max_attempts: Optional[int] = None,
    initial_backoff: Optional[int] = None,
) -> ClaimReportResponse:
    """Call Gemini for a structured evidence report on one claim.

    Mirrors classifier._classify_one's retry-on-429 behavior. Callers pass
    small max_attempts/initial_backoff for interactive requests (fail fast)
    the same way api.py already does for /check.
    """
    from google.genai import types

    max_attempts = max_attempts or config.RETRY_MAX_ATTEMPTS
    backoff = initial_backoff or config.RETRY_INITIAL_BACKOFF_SECONDS

    context_lines = [f"Claim: {claim_text}"]
    if topic:
        context_lines.append(f"Topic: {topic}")
    if fact_check_verdict:
        source_note = f" (source: {fact_check_source})" if fact_check_source else ""
        context_lines.append(f"Existing fact-check verdict: {fact_check_verdict}{source_note}")
    contents = "\n".join(context_lines)

    last_err: Optional[str] = None
    for attempt in range(max_attempts):
        try:
            gemini_limiter.acquire()
            response = client.models.generate_content(
                model=config.MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=REPORT_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    max_output_tokens=config.REPORT_MAX_TOKENS,
                ),
            )
            raw = (response.text or "").strip()
            parsed = _parse_json(raw)
            return _normalize(parsed)
        except Exception as e:
            last_err = str(e)
            is_rate_limit = "429" in last_err or "RESOURCE_EXHAUSTED" in last_err
            if is_rate_limit and attempt < max_attempts - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    raise RuntimeError(last_err or "claim report generation failed")