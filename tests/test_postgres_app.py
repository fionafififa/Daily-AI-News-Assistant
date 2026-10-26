import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db_schema import runs, traces
from app.main import create_app
from app.postgres_store import PostgresStore
from app.schemas import Subscription
from app.storage import create_store


def settings_for(engine, tmp_path, **overrides):
    return Settings(
        data_dir=tmp_path,
        database_url=engine.url.render_as_string(hide_password=False),
        scheduler_enabled=False,
        **overrides,
    )


def wait_run(client, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"completed", "failed"} and run["delivery_status"] in {
            "sent",
            "failed",
            "unknown",
            "not_sent",
        }:
            return run
        time.sleep(0.02)
    pytest.fail("任务未在验收时限内完成")


def test_generation_and_notifications_persist_across_app_restart(postgres_engine, tmp_path):
    settings = settings_for(postgres_engine, tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").json()["storage_backend"] == "postgresql"
        payload = Subscription(name="PostgreSQL 页面验收", enabled=False).model_dump()
        response = client.post("/api/users", json=payload)
        assert response.status_code == 201
        user = response.json()
        accepted = client.post(f"/api/users/{user['id']}/generate")
        assert accepted.status_code == 202
        run_id = accepted.json()["id"]
        run = wait_run(client, run_id)
        assert run["status"] == "completed", run
        assert run["delivery_status"] == "sent"
        assert run["brief"]["items"]
        assert run["traces"]
        messages = app.state.store.notifications(user["id"])
        assert len(messages) == 1
        assert client.get("/api/status").json()["storage_backend"] == "postgresql"
    assert not (tmp_path / "news.sqlite3").exists()

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        assert any(item["id"] == user["id"] for item in client.get("/api/users").json())
        assert client.get(f"/api/runs/{run_id}").json()["brief"] == run["brief"]
        assert restarted.state.store.notifications(user["id"]) == messages
    with postgres_engine.connect() as connection:
        assert connection.execute(select(runs.c.id).where(runs.c.id == run_id)).scalar_one() == run_id
        assert connection.execute(select(traces.c.id).where(traces.c.run_id == run_id)).first() is not None


def test_api_delivery_retry_reuses_brief_and_does_not_duplicate_notification(
    postgres_engine, tmp_path, monkeypatch
):
    settings = settings_for(postgres_engine, tmp_path, smtp_host="local-test", smtp_from="test@example.com")
    attempts = []

    def send(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("local simulated SMTP failure")

    monkeypatch.setattr("app.service.send_email", send)
    app = create_app(settings)
    with TestClient(app) as client:
        payload = Subscription(name="推送重试", channel="email", email="reader@example.com", enabled=False)
        user = client.post("/api/users", json=payload.model_dump()).json()
        run_id = client.post(f"/api/users/{user['id']}/generate").json()["id"]
        first = wait_run(client, run_id)
        assert first["status"] == "completed"
        assert first["delivery_status"] == "failed"
        assert client.post(f"/api/runs/{run_id}/retry-delivery").status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            retried = client.get(f"/api/runs/{run_id}").json()
            if retried["delivery_status"] == "sent":
                break
            time.sleep(0.02)
        assert retried["delivery_status"] == "sent"
        assert retried["brief"] == first["brief"]
        assert retried["traces"] == first["traces"]
        assert len(app.state.store.notifications(user["id"])) == 1
        assert len(attempts) == 2


def test_api_reports_database_failure_without_leaking_error_details(postgres_engine, tmp_path, monkeypatch):
    app = create_app(settings_for(postgres_engine, tmp_path))
    with TestClient(app) as client:

        def unavailable():
            raise OperationalError("private SQL", {"password": "secret-value"}, Exception("private failure"))

        monkeypatch.setattr(app.state.store, "users", unavailable)
        response = client.get("/api/users")
        assert response.status_code == 503
        assert response.json()["detail"] == "数据库操作失败，请稍后重试。"
        assert "secret-value" not in response.text
        assert "private SQL" not in response.text


def test_unmigrated_database_fails_startup_without_sqlite_fallback(postgres_engine, tmp_path):
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE traces")
    app = create_app(settings_for(postgres_engine, tmp_path))
    with pytest.raises(ValueError, match="alembic upgrade head"):
        with TestClient(app):
            pass
    assert isinstance(app.state.store, PostgresStore)
    assert not (tmp_path / "news.sqlite3").exists()


def test_invalid_database_driver_is_rejected_without_fallback(tmp_path):
    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        create_store(Settings(data_dir=tmp_path, database_url="sqlite:///wrong.sqlite3"))
    assert not (tmp_path / "news.sqlite3").exists()


async def test_sse_replays_persisted_notifications_and_honors_cursor(postgres_engine, tmp_path):
    store = PostgresStore(postgres_engine)
    user = store.save_user({"name": "断线重连", "enabled": False})
    first = store.new_run(user["id"], "demo")
    store.update_run(first["id"], status="completed")
    store.notify(user["id"], first["id"], "第一份")
    first_cursor = store.notifications(user["id"])[0]["id"]
    second = store.new_run(user["id"], "demo")
    store.update_run(second["id"], status="completed")
    store.notify(user["id"], second["id"], "第二份")
    app = create_app(settings_for(postgres_engine, tmp_path))
    endpoint = next(
        route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/users/{user_id}/events"
    )

    class Request:
        headers = {"last-event-id": str(first_cursor)}

        async def is_disconnected(self):
            return False

    response = await endpoint(user["id"], Request(), after=0)
    try:
        event = await anext(response.body_iterator)
        data = json.loads(next(line[6:] for line in event.splitlines() if line.startswith("data: ")))
        assert data["run_id"] == second["id"]
    finally:
        await response.body_iterator.aclose()
        app.state.store.close()
