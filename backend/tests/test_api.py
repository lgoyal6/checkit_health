from fastapi.testclient import TestClient

import api
import config


def test_health_is_available_without_model_credentials():
    response = TestClient(api.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_check_requires_a_configured_model_key(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)

    response = TestClient(api.app).post("/check", json={"text": "Vitamin C cures cancer."})

    assert response.status_code == 503
    assert response.json()["detail"] == "GOOGLE_API_KEY not configured"


def test_check_returns_classification_and_fact_check(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(api, "_genai_client", lambda: object())
    monkeypatch.setattr(
        api,
        "_classify_one",
        lambda *_args, **_kwargs: {
            "label": "MEDICAL_CLAIM",
            "claim": "Vitamin C cures cancer.",
            "topic": "treatment",
            "confidence": 0.95,
            "reasoning": "Specific therapeutic assertion.",
        },
    )
    monkeypatch.setattr(api, "is_falsifiable", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        api,
        "check_claim",
        lambda *_args, **_kwargs: {
            "verdict": "False",
            "publisher": "Example Fact Check",
            "url": "https://example.test/fact-check",
        },
    )
    monkeypatch.setattr(api, "_persist_check", lambda *_args, **_kwargs: None)

    response = TestClient(api.app).post("/check", json={"text": "Vitamin C cures cancer."})

    assert response.status_code == 200
    assert response.json()["fact_check_verdict"] == "False"
    assert response.json()["falsifiable"] is True
