import evidence
from evidence import Evidence, retrieve_evidence


def test_hybrid_retrieval_ranks_relevant_authoritative_evidence_first(monkeypatch):
    monkeypatch.setattr(evidence.config, "EVIDENCE_RETRIEVAL_ENABLED", True)
    evidence._CACHE.clear()

    def fake(_claim):
        return [
            Evidence("weak", "Unrelated nutrition news", "Apples are foods.", "u1", "Blog", "news"),
            Evidence(
                "strong", "Ivermectin and COVID-19", "Ivermectin did not reduce mortality.",
                "u2", "Medical Journal", "peer_reviewed_study", authority_score=0.9,
            ),
        ]

    result = retrieve_evidence("Ivermectin cures COVID-19", retrievers=[fake])

    assert result["status"] == "ok"
    assert result["evidence"][0]["id"] == "strong"
    assert result["evidence"][0]["relevance_score"] > result["evidence"][1]["relevance_score"]


def test_retrieval_reports_partial_failure_instead_of_raising(monkeypatch):
    monkeypatch.setattr(evidence.config, "EVIDENCE_RETRIEVAL_ENABLED", True)
    evidence._CACHE.clear()

    def broken(_claim):
        raise evidence.requests.RequestException("offline")

    result = retrieve_evidence("novel claim", retrievers=[broken])
    assert result["status"] == "partial_failure"
    assert result["errors"][0]["source"] == "broken"


def test_retrieval_uses_cache(monkeypatch):
    monkeypatch.setattr(evidence.config, "EVIDENCE_RETRIEVAL_ENABLED", True)
    evidence._CACHE.clear()
    calls = []

    def fake(_claim):
        calls.append(True)
        return [Evidence("x", "Claim evidence", "claim evidence", "u", "NIH", "guideline")]

    retrieve_evidence("claim evidence", retrievers=[fake])
    second = retrieve_evidence("claim evidence", retrievers=[fake])
    assert second["cached"] is True
    assert len(calls) == 1
