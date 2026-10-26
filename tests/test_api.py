import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import Subscription

pytestmark = pytest.mark.parametrize("settings", ["sqlite", "postgresql"], indirect=True)


def test_subscription_generation_and_history_api(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/health").json()["mode"] == "demo"
        payload = Subscription(name="页面用户", topics=["开源模型"], enabled=False).model_dump()
        user = client.post("/api/users", json=payload).json()
        assert user["topics"] == ["开源模型"]
        uid = user["id"]
        result = client.post(f"/api/users/{uid}/generate")
        assert result.status_code == 202
        rid = result.json()["id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            detail = client.get(f"/api/runs/{rid}").json()
            if detail["status"] in {"completed", "failed"} and detail["delivery_status"] in {
                "sent",
                "failed",
                "unknown",
                "not_sent",
            }:
                break
            time.sleep(0.05)
        assert detail["status"] == "completed", detail
        assert len(detail["brief"]["items"]) == 1
        assert detail["brief"]["items"][0]["topic"] == "开源模型"
        assert len(detail["traces"]) >= 5
        assert len(client.get(f"/api/users/{uid}/runs").json()) == 1
        payload["name"] = "新称呼"
        assert client.put(f"/api/users/{uid}", json=payload).json()["name"] == "新称呼"
        assert client.get(f"/api/runs/{rid}").json()["brief"]["preferences"]["name"] == "页面用户"


def test_invalid_preferences_missing_user_and_cross_origin_rejected(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/users", json={"name": "x", "topics": [], "keywords": []}).status_code == 422
        assert client.post("/api/users", json={"name": "x", "delivery_time": "25:90"}).status_code == 422
        assert client.post("/api/users", json={"name": "x", "timezone": "made/up"}).status_code == 422
        assert client.get("/api/users/unknown/runs").status_code == 404
        assert (
            client.post(
                "/api/users", json={"name": "x"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_live_api_refuses_unconfigured_model(settings):
    settings.mode = "live"
    with TestClient(create_app(settings)) as client:
        user = client.post("/api/users", json={"name": "真实用户"}).json()
        response = client.post(f"/api/users/{user['id']}/generate")
        assert response.status_code == 503
        assert client.get(f"/api/users/{user['id']}/runs").json() == []
