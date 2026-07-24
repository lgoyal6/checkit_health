"""Hybrid medical-evidence retrieval with transparent scoring and caching.

The retriever intentionally uses public APIs and a dependency-free hashed
embedding so it works in the existing deployment. Postgres can persist the
returned JSON; deployments with pgvector can later replace ``semantic_score``
without changing the API contract.
"""

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional
from xml.etree import ElementTree

import requests

import config


PUBMED_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
TIMEOUT = 8
_CACHE: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
_LOCK = threading.Lock()
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}


@dataclass
class Evidence:
    id: str
    title: str
    passage: str
    url: str
    publisher: str
    source_type: str
    published_at: Optional[str] = None
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    relevance_score: float = 0.0
    authority_score: float = 0.0


def _tokens(text: str) -> List[str]:
    return [
        word for word in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(word) > 2 and word not in _STOP
    ]


def _vector(text: str, dimensions: int = 128) -> List[float]:
    """Stable feature-hashed embedding suitable for local semantic-ish recall."""
    vec = [0.0] * dimensions
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 else -1.0
        vec[index] += sign
    norm = math.sqrt(sum(value * value for value in vec)) or 1.0
    return [value / norm for value in vec]


def _similarity(left: str, right: str) -> float:
    a, b = _vector(left), _vector(right)
    return max(0.0, sum(x * y for x, y in zip(a, b)))


def _managed_embeddings(texts: List[str], task_type: str) -> Optional[List[List[float]]]:
    """Return Gemini embeddings when configured; otherwise use local fallback."""
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
                task_type=task_type,
                output_dimensionality=config.EMBEDDING_DIMENSIONS,
            ),
        )
        return [list(item.values) for item in response.embeddings]
    except Exception:
        return None


def _lexical(claim: str, text: str) -> float:
    wanted = set(_tokens(claim))
    return len(wanted & set(_tokens(text))) / len(wanted) if wanted else 0.0


def _safe_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_safe_text(item) for item in value)
    return str(value or "").strip()


def retrieve_pubmed(claim: str, limit: int = 5) -> List[Evidence]:
    search = requests.get(
        PUBMED_URL,
        params={"db": "pubmed", "term": claim, "retmode": "json", "retmax": limit},
        timeout=TIMEOUT,
    )
    search.raise_for_status()
    ids = search.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    summary = requests.get(
        PUBMED_SUMMARY_URL,
        params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        timeout=TIMEOUT,
    )
    summary.raise_for_status()
    result = summary.json().get("result", {})
    rows = []
    for pmid in ids:
        item = result.get(pmid) or {}
        title = _safe_text(item.get("title"))
        authors = item.get("authors") or []
        author_names = ", ".join(a.get("name", "") for a in authors[:3])
        rows.append(Evidence(
            id=f"pubmed:{pmid}",
            title=title,
            passage=f"{title} Authors: {author_names}".strip(),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            publisher=_safe_text(item.get("fulljournalname")) or "PubMed",
            source_type="peer_reviewed_study",
            published_at=_safe_text(item.get("pubdate")) or None,
            authority_score=0.9,
        ))
    return rows


def retrieve_trials(claim: str, limit: int = 3) -> List[Evidence]:
    response = requests.get(
        TRIALS_URL,
        params={"query.term": claim, "pageSize": limit, "format": "json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    rows = []
    for study in response.json().get("studies", []):
        protocol = study.get("protocolSection", {})
        ident = protocol.get("identificationModule", {})
        status = protocol.get("statusModule", {})
        design = protocol.get("designModule", {})
        nct = ident.get("nctId")
        if not nct:
            continue
        title = ident.get("briefTitle") or ident.get("officialTitle") or nct
        passage = (
            f"{title}. Study type: {design.get('studyType', 'unknown')}. "
            f"Status: {status.get('overallStatus', 'unknown')}."
        )
        rows.append(Evidence(
            id=f"trial:{nct}",
            title=title,
            passage=passage,
            url=f"https://clinicaltrials.gov/study/{nct}",
            publisher="ClinicalTrials.gov",
            source_type="clinical_trial_registry",
            published_at=status.get("studyFirstPostDateStruct", {}).get("date"),
            authority_score=0.8,
        ))
    return rows


def fact_check_evidence(match: Optional[Dict[str, Any]]) -> List[Evidence]:
    if not match:
        return []
    claim_text = match.get("claim_text") or match.get("verdict") or ""
    return [Evidence(
        id=f"claimreview:{hashlib.sha1((match.get('url') or claim_text).encode()).hexdigest()[:12]}",
        title=f"Published fact-check: {claim_text}",
        passage=(
            f"Rating: {match.get('verdict') or 'not stated'}. "
            f"Reviewed claim: {claim_text}"
        ),
        url=match.get("url") or "",
        publisher=match.get("publisher") or "ClaimReview publisher",
        source_type="published_fact_check",
        published_at=match.get("review_date"),
        authority_score=0.65,
    )]


def _rank(claim: str, rows: Iterable[Evidence], limit: int) -> tuple[List[Evidence], List[List[float]]]:
    unique: Dict[str, Evidence] = {}
    rows = list(rows)
    texts = [f"{row.title} {row.passage}" for row in rows]
    query_embedding = _managed_embeddings([claim], "RETRIEVAL_QUERY")
    document_embeddings = _managed_embeddings(texts, "RETRIEVAL_DOCUMENT")
    stored_embeddings: List[List[float]] = []
    for index, row in enumerate(rows):
        text = f"{row.title} {row.passage}"
        row.lexical_score = round(_lexical(claim, text), 3)
        if query_embedding and document_embeddings:
            row.semantic_score = round(
                max(0.0, sum(
                    x * y for x, y in zip(query_embedding[0], document_embeddings[index])
                )),
                3,
            )
            stored_embeddings.append(document_embeddings[index])
        else:
            row.semantic_score = round(_similarity(claim, text), 3)
            stored_embeddings.append(_vector(text))
        row.relevance_score = round(
            0.5 * row.lexical_score
            + 0.35 * row.semantic_score
            + 0.15 * row.authority_score,
            3,
        )
        if row.url and (row.url not in unique or row.relevance_score > unique[row.url].relevance_score):
            unique[row.url] = row
    ranked = sorted(unique.values(), key=lambda row: row.relevance_score, reverse=True)[:limit]
    embedding_by_url = {
        row.url: stored_embeddings[index] for index, row in enumerate(rows)
    }
    return ranked, [embedding_by_url[row.url] for row in ranked]


def retrieve_evidence(
    claim: str,
    fact_check: Optional[Dict[str, Any]] = None,
    limit: int = 8,
    retrievers: Optional[List[Callable[[str], List[Evidence]]]] = None,
) -> Dict[str, Any]:
    key = hashlib.sha256(claim.strip().lower().encode()).hexdigest()
    now = time.time()
    with _LOCK:
        cached = _CACHE.get(key)
    if cached and now - cached[0] < config.EVIDENCE_CACHE_TTL_SECONDS:
        return {"status": "ok", "cached": True, "evidence": cached[1], "errors": []}

    rows = fact_check_evidence(fact_check)
    errors = []
    if config.DATABASE_URL:
        try:
            import storage

            query_embedding = _managed_embeddings([claim], "RETRIEVAL_QUERY")
            if query_embedding:
                rows.extend(
                    Evidence(**item)
                    for item in storage.search_evidence(query_embedding[0], limit=limit)
                )
        except Exception as exc:
            errors.append({"source": "pgvector", "error": type(exc).__name__})
    sources = retrievers if retrievers is not None else [retrieve_pubmed, retrieve_trials]
    if config.EVIDENCE_RETRIEVAL_ENABLED:
        for retriever in sources:
            try:
                rows.extend(retriever(claim))
            except (requests.RequestException, ValueError, KeyError, ElementTree.ParseError) as exc:
                errors.append({"source": retriever.__name__, "error": type(exc).__name__})
    ranked_rows, embeddings = _rank(claim, rows, limit)
    ranked = [asdict(row) for row in ranked_rows]
    if config.DATABASE_URL and ranked:
        try:
            import storage

            storage.upsert_evidence(ranked, embeddings)
        except Exception as exc:
            errors.append({"source": "pgvector_write", "error": type(exc).__name__})
    status = "ok" if ranked else ("partial_failure" if errors else "no_evidence")
    with _LOCK:
        _CACHE[key] = (now, ranked)
    return {"status": status, "cached": False, "evidence": ranked, "errors": errors}


def evidence_json(evidence: List[Dict[str, Any]]) -> str:
    return json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
