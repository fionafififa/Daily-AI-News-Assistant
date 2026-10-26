from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db_schema import runs
from app.postgres_store import PostgresStore
from app.storage import utcnow


@pytest.fixture
def postgres_store(postgres_engine):
    store = PostgresStore(postgres_engine)
    store.ensure_ready()
    return store


def test_run_dates_brief_and_history_have_existing_api_shape(postgres_store):
    user = postgres_store.save_user({"name": "完整存储验收"})
    run = postgres_store.new_run(user["id"], "demo", "scheduled", "2026-10-26")
    assert run["status"] == "queued"
    assert run["scheduled_date"] == "2026-10-26"
    assert run["delivery_status"] == "pending"
    assert run["brief"] is None
    assert datetime.fromisoformat(run["created_at"]).utcoffset() is not None
    completed_at = utcnow()
    brief = {"title": "数据库简报", "items": [{"summary": "结构化摘要"}]}
    postgres_store.update_run(run["id"], status="completed", completed_at=completed_at, brief=brief)
    finished = postgres_store.run(run["id"])
    assert finished["brief"] == brief
    assert datetime.fromisoformat(finished["completed_at"]) == datetime.fromisoformat(completed_at)
    assert postgres_store.runs(user["id"]) == [finished]
    second = postgres_store.new_run(user["id"], "demo")
    assert [row["id"] for row in postgres_store.runs(user["id"], 1)] == [second["id"]]
    assert postgres_store.run("unknown") is None


def test_only_expected_task_conflicts_return_none(postgres_store, monkeypatch):
    user = postgres_store.save_user({"name": "任务冲突"})
    run = postgres_store.new_run(user["id"], "demo", "scheduled", "2026-10-26")
    assert postgres_store.new_run(user["id"], "demo") is None
    postgres_store.update_run(run["id"], status="completed")
    assert postgres_store.new_run(user["id"], "demo", "scheduled", "2026-10-26") is None
    with pytest.raises(KeyError):
        postgres_store.new_run("unknown", "demo")
    with monkeypatch.context() as patch:
        patch.setattr("app.postgres_store.uuid4", lambda: UUID(run["id"]))
        with pytest.raises(IntegrityError) as error:
            postgres_store.new_run(user["id"], "demo")
        assert error.value.orig.diag.constraint_name == "pk_runs"
    assert len(postgres_store.runs(user["id"])) == 1


def test_failed_run_releases_active_slot_and_keeps_history(postgres_store):
    user = postgres_store.save_user({"name": "失败后重试"})
    first = postgres_store.new_run(user["id"], "demo")
    postgres_store.update_run(first["id"], status="failed", completed_at=utcnow(), error="测试故障")
    second = postgres_store.new_run(user["id"], "demo")
    assert second["id"] != first["id"]
    assert postgres_store.run(first["id"])["error"] == "测试故障"
    assert len(postgres_store.runs(user["id"])) == 2


def test_invalid_task_update_is_rejected_without_changing_record(postgres_store):
    user = postgres_store.save_user({"name": "更新范围"})
    run = postgres_store.new_run(user["id"], "demo")
    with pytest.raises(ValueError):
        postgres_store.update_run(run["id"], user_id="different")
    with pytest.raises(ValueError):
        postgres_store.update_run(run["id"])
    assert postgres_store.run(run["id"]) == run


def test_trace_preserves_bounded_arguments_and_text_results(postgres_store):
    user = postgres_store.save_user({"name": "工具调用记录"})
    run = postgres_store.new_run(user["id"], "demo")
    postgres_store.trace(run["id"], "normal", {"query": "Agent"}, {"ok": True}, "ok", 2)
    postgres_store.trace(run["id"], "large", {"content": "x" * 30000}, {"text": "y" * 30000}, "error", 5)
    records = postgres_store.traces(run["id"])
    assert records[0]["arguments"] == {"query": "Agent"}
    assert records[0]["result"] == '{"ok": true}'
    assert len(records[1]["arguments"]["truncated_preview"]) == 24000
    assert len(records[1]["result"]) == 24000
    assert records[1]["id"] > records[0]["id"]
    assert datetime.fromisoformat(records[0]["created_at"]).utcoffset() is not None
    assert postgres_store.traces("unknown") == []


def test_delivery_claim_is_atomic_and_retryable_after_failure(postgres_store):
    user = postgres_store.save_user({"name": "原子推送抢占"})
    run = postgres_store.new_run(user["id"], "demo")
    assert not postgres_store.claim_delivery(run["id"])
    postgres_store.update_run(run["id"], status="completed")
    gate = Barrier(2)

    def claim():
        gate.wait(timeout=5)
        return postgres_store.claim_delivery(run["id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [False, True]
    assert postgres_store.run(run["id"])["delivery_status"] == "sending"
    postgres_store.update_run(run["id"], delivery_status="failed", delivery_error="重试前的错误")
    assert postgres_store.claim_delivery(run["id"])
    assert postgres_store.run(run["id"])["delivery_error"] is None
    postgres_store.update_run(run["id"], delivery_status="sent")
    assert not postgres_store.claim_delivery(run["id"])


def test_notifications_are_deduplicated_and_replayed_by_cursor(postgres_store):
    user = postgres_store.save_user({"name": "通知游标"})
    other = postgres_store.save_user({"name": "其他订阅"})
    run = postgres_store.new_run(user["id"], "demo")
    postgres_store.update_run(run["id"], status="completed")
    postgres_store.notify(user["id"], run["id"], "第一份简报")
    postgres_store.notify(user["id"], run["id"], "重复推送")
    first = postgres_store.notifications(user["id"])
    assert len(first) == 1
    assert first[0]["title"] == "第一份简报"
    assert postgres_store.notifications(user["id"], first[0]["id"]) == []
    second = postgres_store.new_run(user["id"], "demo")
    postgres_store.update_run(second["id"], status="completed")
    postgres_store.notify(user["id"], second["id"], "第二份简报")
    replay = postgres_store.notifications(user["id"], first[0]["id"])
    assert [item["run_id"] for item in replay] == [second["id"]]
    assert postgres_store.notifications(other["id"]) == []
    assert isinstance(replay[0]["created_at"], str)


def test_recovery_preserves_briefs_and_marks_uncertain_delivery(postgres_store):
    first = postgres_store.save_user({"name": "中断任务"})
    queued = postgres_store.new_run(first["id"], "demo")
    second = postgres_store.save_user({"name": "运行中任务"})
    running = postgres_store.new_run(second["id"], "demo")
    postgres_store.update_run(running["id"], status="running")
    completed = []
    for status in ("pending", "sending", "sent"):
        user = postgres_store.save_user({"name": status})
        run = postgres_store.new_run(user["id"], "demo")
        postgres_store.update_run(
            run["id"], status="completed", brief={"title": status}, delivery_status=status
        )
        completed.append(run)
    postgres_store.recover()
    for run in (queued, running):
        assert postgres_store.run(run["id"])["status"] == "failed"
    for run, expected in zip(completed, ("unknown", "unknown", "sent"), strict=True):
        saved = postgres_store.run(run["id"])
        assert saved["status"] == "completed"
        assert saved["brief"] is not None
        assert saved["delivery_status"] == expected


def test_missing_schema_fails_with_actionable_message(postgres_store):
    with postgres_store.engine.begin() as connection:
        connection.execute(text("DROP TABLE traces"))
    with pytest.raises(ValueError, match="alembic upgrade head"):
        postgres_store.ensure_ready()


def test_clearing_brief_writes_sql_null(postgres_store):
    user = postgres_store.save_user({"name": "空简报字段"})
    run = postgres_store.new_run(user["id"], "demo")
    postgres_store.update_run(run["id"], brief={"title": "草稿"})
    postgres_store.update_run(run["id"], brief=None)
    with postgres_store.engine.connect() as connection:
        assert connection.execute(select(runs.c.id).where(runs.c.brief.is_(None))).scalar_one() == run["id"]
