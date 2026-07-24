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
    monkeypatch.setattr(
        api,
        "retrieve_evidence",
        lambda *_args, **_kwargs: {"status": "ok", "evidence": [], "errors": []},
    )

    response = TestClient(api.app).post("/check", json={"text": "Vitamin C cures cancer."})

    assert response.status_code == 200
    assert response.json()["fact_check_verdict"] == "False"
    assert response.json()["falsifiable"] is True
    assert response.json()["assessment"]["cluster_id"]
    assert response.json()["assessment"]["audit"]["final_judgment"] == "human"


def test_root_describes_the_service():
    response = TestClient(api.app).get("/")

    assert response.status_code == 200
    assert response.json()["service"] == "Checkit Health API"


def test_check_rejects_empty_text():
    response = TestClient(api.app).post("/check", json={"text": "   "})

    assert response.status_code == 422


def test_check_surfaces_a_classifier_error_as_503(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(api, "_genai_client", lambda: object())
    monkeypatch.setattr(
        api,
        "_classify_one",
        lambda *_a, **_k: {"label": "NOISE", "confidence": None, "error": "429"},
    )

    response = TestClient(api.app).post("/check", json={"text": "anything"})

    assert response.status_code == 503


def test_check_skips_fact_check_for_noise(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(api, "_genai_client", lambda: object())
    monkeypatch.setattr(
        api,
        "_classify_one",
        lambda *_a, **_k: {
            "label": "NOISE",
            "claim": None,
            "topic": None,
            "confidence": None,
            "reasoning": "not a claim",
        },
    )

    response = TestClient(api.app).post("/check", json={"text": "go team!"})

    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "NOISE"
    assert body["falsifiable"] is None
    assert body["fact_check_verdict"] is None


def test_report_rejects_an_empty_claim():
    response = TestClient(api.app).post("/report", json={"claim": "  "})

    assert response.status_code == 422


def test_report_requires_a_configured_model_key(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_API_KEY", None)

    response = TestClient(api.app).post("/report", json={"claim": "X cures Y"})

    assert response.status_code == 503


def test_history_falls_back_to_empty_without_a_table(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_URL", None)
    monkeypatch.setattr(config, "DEFAULT_DB_PATH", str(tmp_path / "empty.db"))

    response = TestClient(api.app).get("/history")

    assert response.status_code == 200
    assert response.json() == []


def test_monitor_is_empty_without_postgres(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", None)

    response = TestClient(api.app).get("/monitor")

    assert response.status_code == 200
    assert response.json() == []


def test_stats_are_empty_without_postgres(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", None)

    response = TestClient(api.app).get("/stats")

    assert response.status_code == 200
    assert response.json() == {}


def test_health_db_reports_unconfigured_postgres(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", None)

    response = TestClient(api.app).get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"postgres_configured": False, "ok": False}
