"""Counter-message drafting. Drafts only, never publishing.

Checkit's whole position is that it assembles evidence and a qualified human
decides. A counter-post is a verdict, published, so this module is where that
position is either kept or lost. It is kept by five constraints, all enforced
in code rather than in a prompt:

1. **Gated on evidence.** Generation is refused unless the narrative's evidence
   state is ``contradicted`` or ``mixed``. If the retrieval came back
   ``insufficient``, there is no button. A tool that can write a rebuttal for a
   claim it has no evidence against is a misinformation generator.
2. **Protocol-structured, not free-written.** The draft fills the slots of the
   Debunking Handbook 2020 fact-myth-fallacy structure, or the inoculation
   structure for pre-bunking. The model is filling a form designed by
   misinformation researchers, not composing an argument.
3. **Citations validated.** Every citation id is checked against the evidence
   actually retrieved, the same way ``claim_report._normalize`` does it. An
   invented source is dropped, and a draft left with no citations is refused.
4. **Approval gate.** A draft is ``draft`` until a named human moves it to
   ``approved`` or ``rejected``. Only approved drafts can be exported.
5. **No posting integration.** There is no OAuth, no scheduler, no send. Export
   is text. The moment this system can post, it stops being decision support
   and becomes a moderation actor with a completely different liability model.

Mode is chosen from where the narrative sits on its curve, not by the user.
Repeating a myth to an audience that has not encountered it spreads it, so a
rumor that is still emerging gets a pre-bunk that teaches the manipulation
tactic without restating the claim, and only a rumor that is already widely
seen gets a direct debunk. A declining narrative gets nothing: responding to a
dying rumor revives it.
"""

import time
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

import config
from classifier import _parse_json
from rate_limiter import gemini_limiter

DEBUNK = "debunk"
PREBUNK = "prebunk"

PROTOCOLS = {
    DEBUNK: "debunking-handbook-2020/fact-myth-fallacy",
    PREBUNK: "inoculation/tactic-first",
}

# Which response is appropriate at each point on the narrative's curve.
MODE_BY_LIFECYCLE = {
    "emerging": PREBUNK,
    "accelerating": PREBUNK,
    "peaking": DEBUNK,
    "watching": DEBUNK,
    "declining": None,
    "dormant": None,
}


class ResponseDraft(BaseModel):
    """The Debunking Handbook slots, as a typed schema.

    The multilayered structure is fact first, then a warning, then the myth
    stated once, then why it is wrong, then a causal replacement, then the fact
    again. Each field maps to one of those slots so the output cannot skip the
    parts that make a correction stick.
    """

    mode: str
    fact: str = ""
    warning: str = ""
    myth: str = ""
    fallacy_name: str = ""
    explanation: str = ""
    replacement: str = ""
    reinforcement: str = ""
    suggested_post: str = ""
    citations: List[str] = Field(default_factory=list)
    dropped_citations: List[str] = Field(default_factory=list)
    tone_notes: List[str] = Field(default_factory=list)


class ResponseRefusal(Exception):
    """Raised when a draft must not be generated. Carries a plain reason."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


DEBUNK_PROMPT = """You draft corrections for public health communicators, \
following the Debunking Handbook 2020 fact-myth-fallacy structure.

Structure, in order:
- fact: the accurate information, stated plainly and first. Never open with the myth.
- warning: one short line warning the reader that a myth follows.
- myth: the false claim, stated exactly once, briefly, never repeated afterward.
- fallacy_name: the specific reasoning error the myth relies on (for example \
"correlation mistaken for causation", "cherry-picked data", "false authority", \
"anecdote as evidence").
- explanation: why the myth is wrong and how the fallacy operates, grounded in \
the supplied evidence.
- replacement: what actually explains the thing the myth claims to explain. A \
correction fails when it leaves a gap, so give the reader something true to \
put in place of what you removed.
- reinforcement: restate the fact, closing on truth rather than on the myth.
- suggested_post: the whole thing as one social post under 280 characters, \
plain language, no hashtags, no emoji.

Rules:
- Use ONLY the supplied numbered evidence passages. Never use memory.
- Every factual sentence must end with citation ids in square brackets, for \
example [pubmed:123]. Never invent an id.
- Do not overstate. "Current evidence does not show X" unless the evidence \
actually contradicts the claim.
- Never imply the reader is gullible, never be sarcastic, never scold. Assume \
someone shared this because they were worried, not because they are foolish.
- Do not induce blanket distrust of health information; correct one claim.
- Plain language, roughly an eighth-grade reading level.

Respond ONLY with a single JSON object, no prose, no markdown fences:
{"fact": string, "warning": string, "myth": string, "fallacy_name": string,
 "explanation": string, "replacement": string, "reinforcement": string,
 "suggested_post": string, "citations": [string, ...]}
"""

PREBUNK_PROMPT = """You draft pre-bunking messages for public health \
communicators. A rumor is starting to spread but most people have not seen it \
yet, so restating it would introduce it to an audience that would otherwise \
never encounter it.

Inoculate against the TECHNIQUE instead of rebutting the specific claim.

- fact: the accurate background a reader needs, stated first.
- warning: forewarn that misleading claims of a particular shape are circulating.
- myth: leave this an EMPTY STRING. Do not restate the rumor.
- fallacy_name: the manipulation technique to inoculate against.
- explanation: how that technique works in general, using a weakened, generic \
example rather than the live rumor.
- replacement: what a reader should look for instead to judge a claim like this.
- reinforcement: restate the fact.
- suggested_post: the whole thing as one social post under 280 characters, \
plain language, no hashtags, no emoji.

Rules:
- Use ONLY the supplied numbered evidence passages. Never use memory.
- Every factual sentence must end with citation ids in square brackets.
- Never name or quote the specific rumor. `myth` MUST be an empty string.
- Never imply the reader is gullible. Do not induce blanket distrust of health \
information.
- Plain language, roughly an eighth-grade reading level.

Respond ONLY with a single JSON object, no prose, no markdown fences:
{"fact": string, "warning": string, "myth": "", "fallacy_name": string,
 "explanation": string, "replacement": string, "reinforcement": string,
 "suggested_post": string, "citations": [string, ...]}
"""

TONE_NOTES = [
    "Lead with the fact; never open on the myth.",
    "State the myth at most once, and never in a headline.",
    "Do not imply the audience is gullible for having seen this.",
    "Correct one claim; avoid language that erodes trust in health information generally.",
]


def mode_for_lifecycle(lifecycle_state: Optional[str]) -> Optional[str]:
    """Which response mode, if any, fits this point on the narrative's curve."""
    return MODE_BY_LIFECYCLE.get((lifecycle_state or "watching").lower(), DEBUNK)


def check_preconditions(
    evidence_state: Optional[str],
    evidence: Sequence[Dict[str, Any]],
    lifecycle_state: Optional[str],
) -> str:
    """Decide whether a draft may be generated, and in which mode.

    Raises ResponseRefusal with a reason an analyst can act on. This is the
    gate: everything downstream assumes it passed.
    """
    if not config.RESPONSE_ENABLED:
        raise ResponseRefusal(
            "response_drafting_disabled",
            "Counter-message drafting is turned off in this deployment.",
        )
    state = (evidence_state or "insufficient").lower()
    if state not in config.RESPONSE_ALLOWED_EVIDENCE_STATES:
        raise ResponseRefusal(
            "insufficient_evidence",
            f"Evidence state is '{state}'. A response can only be drafted when "
            "retrieved evidence actually contradicts the claim or is mixed. "
            "A missing match is not grounds for a rebuttal.",
        )
    if not evidence:
        raise ResponseRefusal(
            "no_sources",
            "No evidence passages are attached to this narrative, so there is "
            "nothing for a draft to cite.",
        )
    mode = mode_for_lifecycle(lifecycle_state)
    if mode is None:
        raise ResponseRefusal(
            "narrative_declining",
            f"This narrative is '{lifecycle_state}'. Responding to a rumor that "
            "is already fading tends to revive it. Archive it instead.",
        )
    return mode


def _validate_citations(
    parsed: Dict[str, Any], evidence: Sequence[Dict[str, Any]]
) -> tuple:
    """Keep only citation ids that appear in the retrieved evidence."""
    allowed = {str(item.get("id")) for item in evidence if item.get("id")}
    raw = [str(c) for c in (parsed.get("citations") or [])]
    kept = [c for c in raw if c in allowed]
    dropped = [c for c in raw if c not in allowed]
    return kept, dropped


def _evidence_block(evidence: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for item in evidence:
        lines.append(
            f"[{item.get('id')}] {item.get('title')}. {item.get('passage')} "
            f"Publisher: {item.get('publisher')}; "
            f"date: {item.get('published_at') or 'unknown'}"
        )
    return "\n".join(lines)


def generate_draft(
    client: Any,
    claim: str,
    evidence: Sequence[Dict[str, Any]],
    mode: str,
    max_attempts: int = 2,
    initial_backoff: int = 2,
) -> ResponseDraft:
    """Fill the protocol's slots from the retrieved evidence.

    Mirrors claim_report.generate_claim_report's retry behavior so a throttled
    model returns a quick "try again" rather than hanging on the analyst.
    """
    from google.genai import types

    prompt = PREBUNK_PROMPT if mode == PREBUNK else DEBUNK_PROMPT
    contents = f"Claim circulating: {claim}\nEvidence passages:\n{_evidence_block(evidence)}"

    backoff = initial_backoff
    last_err: Optional[str] = None
    for attempt in range(max_attempts):
        try:
            gemini_limiter.acquire()
            response = client.models.generate_content(
                model=config.MODEL_NAME,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    response_mime_type="application/json",
                    max_output_tokens=config.RESPONSE_MAX_TOKENS,
                ),
            )
            parsed = _parse_json((response.text or "").strip())
            return _normalize(parsed, evidence, mode)
        except ResponseRefusal:
            raise
        except Exception as e:
            last_err = str(e)
            if ("429" in last_err or "RESOURCE_EXHAUSTED" in last_err) and attempt < max_attempts - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    raise RuntimeError(last_err or "response drafting failed")


def _normalize(
    parsed: Dict[str, Any], evidence: Sequence[Dict[str, Any]], mode: str
) -> ResponseDraft:
    citations, dropped = _validate_citations(parsed, evidence)
    if not citations:
        raise ResponseRefusal(
            "no_valid_citations",
            "The draft cited no source that was actually retrieved, so it was "
            "discarded rather than shown.",
        )

    def field(name: str) -> str:
        return str(parsed.get(name) or "").strip()

    myth = "" if mode == PREBUNK else field("myth")
    return ResponseDraft(
        mode=mode,
        fact=field("fact"),
        warning=field("warning"),
        myth=myth,
        fallacy_name=field("fallacy_name"),
        explanation=field("explanation"),
        replacement=field("replacement"),
        reinforcement=field("reinforcement"),
        suggested_post=field("suggested_post")[:400],
        citations=citations,
        dropped_citations=dropped,
        tone_notes=list(TONE_NOTES),
    )


def render_plain_text(draft: Dict[str, Any], narrative_label: str = "") -> str:
    """An approved draft as copyable plain text.

    Export is text on purpose: there is nothing here that sends, schedules, or
    authenticates to a platform, so an approved draft always passes through a
    person before it reaches an audience.
    """
    order = [
        ("fact", "FACT"),
        ("warning", "WARNING"),
        ("myth", "MYTH"),
        ("fallacy_name", "FALLACY"),
        ("explanation", "WHY IT IS WRONG"),
        ("replacement", "WHAT IS ACTUALLY TRUE"),
        ("reinforcement", "FACT, RESTATED"),
    ]
    lines = []
    if narrative_label:
        lines.append(f"Narrative: {narrative_label}")
    lines.append(f"Mode: {draft.get('mode', '')}")
    lines.append(f"Protocol: {PROTOCOLS.get(draft.get('mode'), 'unknown')}")
    lines.append("")
    for key, heading in order:
        value = (draft.get(key) or "").strip()
        if value:
            lines.append(f"{heading}: {value}")
    if draft.get("suggested_post"):
        lines += ["", "SUGGESTED POST:", draft["suggested_post"]]
    if draft.get("citations"):
        lines += ["", "SOURCES: " + ", ".join(draft["citations"])]
    lines += [
        "",
        "Reviewed and approved by a human before use. Checkit Health does not "
        "publish; this text is exported for a person to send.",
    ]
    return "\n".join(lines)
