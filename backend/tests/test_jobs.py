import time

from fastapi.testclient import TestClient

import api


def test_background_check_completes(monkeypatch):
    monkeypatch.setattr(
        api,
        "_run_check",
        lambda text: api.CheckResponse(label="NOISE", reasoning=text),
    )
    client = TestClient(api.app)
    queued = client.post("/jobs/check", json={"text": "hello"})
    assert queued.status_code == 202
    job_id = queued.json()["id"]
    for _ in range(50):
        result = client.get(f"/jobs/{job_id}").json()
        if result["status"] == "completed":
            break
        time.sleep(0.01)
    assert result["result"]["reasoning"] == "hello"


def test_unknown_job_is_404():
    response = TestClient(api.app).get("/jobs/not-real")
    assert response.status_code == 404
