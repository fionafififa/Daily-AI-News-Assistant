import asyncio
from datetime import UTC, datetime

from app.schemas import Subscription
from app.service import NewsService


async def drain(service):
    if service.tasks:
        await asyncio.gather(*list(service.tasks))


async def test_demo_generation_inbox_and_history_dedup(settings, store, user):
    service = NewsService(settings, store)
    run = service.enqueue(user["id"])
    assert service.enqueue(user["id"]) is None  # Concurrent manual request is refused.
    await drain(service)
    finished = store.run(run["id"])
    assert finished["status"] == "completed", finished
    assert len(finished["brief"]["items"]) == 2
    assert finished["delivery_status"] == "sent"
    assert any(trace["tool"] == "fetch_news" and trace["status"] == "ok" for trace in store.traces(run["id"]))
    assert store.notifications(user["id"])[0]["run_id"] == run["id"]
    await service.deliver(run["id"])
    assert len(store.notifications(user["id"])) == 1
    second = service.enqueue(user["id"])
    await drain(service)
    assert store.run(second["id"])["brief"]["items"] == []
    assert len(store.runs(user["id"])) == 2


async def test_scheduler_timezone_paused_users_and_daily_idempotence(settings, store):
    active = store.save_user(Subscription(name="上海", enabled=True, delivery_time="08:00").model_dump())
    paused = store.save_user(Subscription(name="暂停", enabled=False, delivery_time="00:00").model_dump())
    service = NewsService(settings, store)
    await service.tick(datetime(2026, 10, 17, 23, 59, tzinfo=UTC))
    assert store.runs(active["id"]) == []
    now = datetime(2026, 10, 18, 0, 1, tzinfo=UTC)
    await service.tick(now)
    await service.tick(now)
    await drain(service)
    await service.tick(now)
    assert len(store.runs(active["id"])) == 1
    assert store.runs(active["id"])[0]["scheduled_date"] == "2026-10-18"
    assert store.runs(paused["id"]) == []
    # Reconstructing the service retains daily claims in SQLite.
    restarted = NewsService(settings, store)
    await restarted.tick(now)
    assert not restarted.tasks


async def test_timeout_is_failure_not_empty_brief(settings, store, user):
    settings.mode = "live"
    settings.agent_timeout = 0.01

    async def hanging_runner(box):
        await asyncio.sleep(1)

    service = NewsService(settings, store, live_runner=hanging_runner)
    run = service.enqueue(user["id"])
    await drain(service)
    finished = store.run(run["id"])
    assert finished["status"] == "failed"
    assert "超时" in finished["error"]
    assert finished["brief"] is None
    assert store.notifications(user["id"]) == []


async def test_smtp_failure_keeps_brief_and_retry_does_not_regenerate(settings, store, user, monkeypatch):
    store.save_user(
        Subscription(
            name=user["name"], channel="email", email="reader@example.com", enabled=False
        ).model_dump(),
        user["id"],
    )
    sent = []

    def send(settings, user, run):
        sent.append(run["id"])
        if len(sent) == 1:
            raise ConnectionError("temporary failure")

    monkeypatch.setattr("app.service.send_email", send)
    service = NewsService(settings, store)
    run = service.enqueue(user["id"])
    await drain(service)
    finished = store.run(run["id"])
    assert finished["status"] == "completed"
    assert finished["delivery_status"] == "failed"
    tool_calls = len(store.traces(run["id"]))
    await service.deliver(run["id"])
    assert store.run(run["id"])["delivery_status"] == "sent"
    assert len(store.traces(run["id"])) == tool_calls
    assert len(store.notifications(user["id"])) == 1


def test_restart_recovery_marks_interrupted_and_unknown_delivery(store, user):
    run = store.new_run(user["id"], "demo")
    store.recover()
    assert store.run(run["id"])["status"] == "failed"
    store.update_run(run["id"], status="completed", delivery_status="sending")
    store.recover()
    assert store.run(run["id"])["delivery_status"] == "unknown"
