"""Real PostgreSQL acceptance for the related tables and database business rules."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db_schema import notifications, runs, traces, users
from app.postgres_subscriptions import PostgresSubscriptions


def run_values(user_id, **overrides):
    return {
        "id": uuid4().hex,
        "user_id": user_id,
        "mode": "demo",
        "status": "queued",
        "trigger": "manual",
        "created_at": datetime.now(UTC),
    } | overrides


def create_run(engine, user_id, **overrides):
    with engine.begin() as connection:
        row = connection.execute(insert(runs).values(**run_values(user_id, **overrides)).returning(runs))
        saved = dict(row.mappings().one())
    return saved


def test_related_records_round_trip_with_json_dates_and_defaults(postgres_engine):
    subscription = PostgresSubscriptions(postgres_engine).save_user({"name": "关联数据验收"})
    scheduled = date(2026, 10, 26)
    run = create_run(
        postgres_engine, subscription["id"], trigger="scheduled", scheduled_date=scheduled, brief=None
    )
    assert run["delivery_status"] == "pending"
    assert run["scheduled_date"] == scheduled
    assert run["created_at"].utcoffset() is not None
    with postgres_engine.connect() as connection:
        assert connection.execute(select(runs.c.id).where(runs.c.brief.is_(None))).scalar_one() == run["id"]

    brief = {"title": "完成的简报", "items": [{"summary": "中文证据"}]}
    arguments = {"source": "O'Brien", "topics": ["Agent"]}
    incomplete_json = '{"truncated_result":'
    with postgres_engine.begin() as connection:
        trace_id = connection.execute(
            insert(traces)
            .values(
                run_id=run["id"],
                tool="fetch_news",
                arguments=arguments,
                result=incomplete_json,
                status="ok",
                duration_ms=12,
                created_at=datetime.now(UTC),
            )
            .returning(traces.c.id)
        ).scalar_one()
        connection.execute(
            update(runs)
            .where(runs.c.id == run["id"])
            .values(status="completed", completed_at=datetime.now(UTC), brief=brief)
        )
        notification_id = connection.execute(
            insert(notifications)
            .values(
                user_id=subscription["id"],
                run_id=run["id"],
                title=brief["title"],
                created_at=datetime.now(UTC),
            )
            .returning(notifications.c.id)
        ).scalar_one()

    assert trace_id > 0
    assert notification_id > 0
    with postgres_engine.connect() as connection:
        saved_trace = connection.execute(select(traces).where(traces.c.id == trace_id)).mappings().one()
        saved_run = connection.execute(select(runs).where(runs.c.id == run["id"])).mappings().one()
        saved_notification = (
            connection.execute(select(notifications).where(notifications.c.id == notification_id))
            .mappings()
            .one()
        )
    assert saved_trace["arguments"] == arguments
    assert saved_trace["result"] == incomplete_json
    assert saved_run["brief"] == brief
    assert saved_run["completed_at"].utcoffset() is not None
    assert saved_notification["user_id"] == saved_run["user_id"]


@pytest.mark.parametrize("record_type", ["run", "trace", "notification"])
def test_orphan_records_are_rejected(postgres_engine, record_type):
    if record_type == "run":
        statement = insert(runs).values(**run_values("missing-subscription"))
    elif record_type == "trace":
        statement = insert(traces).values(
            run_id="missing-run",
            tool="read_article",
            arguments={},
            result="error",
            status="error",
            duration_ms=1,
            created_at=datetime.now(UTC),
        )
    else:
        statement = insert(notifications).values(
            user_id="missing-subscription",
            run_id="missing-run",
            title="不存在的任务",
            created_at=datetime.now(UTC),
        )
    with pytest.raises(IntegrityError) as error:
        with postgres_engine.begin() as connection:
            connection.execute(statement)
    assert error.value.orig.sqlstate == "23503"  # foreign_key_violation


def test_notification_must_belong_to_its_run_owner(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    owner = subscriptions.save_user({"name": "任务所属订阅"})
    other = subscriptions.save_user({"name": "其他订阅"})
    run = create_run(postgres_engine, owner["id"], status="completed")
    with pytest.raises(IntegrityError) as error:
        with postgres_engine.begin() as connection:
            connection.execute(
                insert(notifications).values(
                    user_id=other["id"], run_id=run["id"], title="归属错误", created_at=datetime.now(UTC)
                )
            )
    assert error.value.orig.diag.constraint_name == "fk_notifications_run_owner"
    with postgres_engine.connect() as connection:
        assert connection.execute(select(notifications.c.id)).all() == []


def test_a_run_can_have_only_one_inbox_notification(postgres_engine):
    subscription = PostgresSubscriptions(postgres_engine).save_user({"name": "通知去重"})
    run = create_run(postgres_engine, subscription["id"], status="completed")
    values = {
        "user_id": subscription["id"],
        "run_id": run["id"],
        "title": "同一份简报",
        "created_at": datetime.now(UTC),
    }
    with postgres_engine.begin() as connection:
        first_id = connection.execute(insert(notifications).values(**values).returning(notifications.c.id))
        first_id = first_id.scalar_one()
    with pytest.raises(IntegrityError) as error:
        with postgres_engine.begin() as connection:
            connection.execute(insert(notifications).values(**values))
    assert error.value.orig.diag.constraint_name == "uq_notifications_run_id"
    with postgres_engine.connect() as connection:
        assert connection.execute(select(notifications.c.id)).scalars().all() == [first_id]


@pytest.mark.parametrize("statuses", [("queued", "queued"), ("queued", "running"), ("running", "running")])
def test_concurrent_active_runs_have_one_winner(postgres_engine, statuses):
    subscription = PostgresSubscriptions(postgres_engine).save_user({"name": "并发任务验收"})
    gate = Barrier(2)

    def submit(status):
        values = run_values(subscription["id"], status=status)
        try:
            with postgres_engine.begin() as connection:
                gate.wait(timeout=5)
                connection.execute(insert(runs).values(**values))
            return "created", values["id"]
        except IntegrityError as exc:
            return "conflict", exc.orig.diag.constraint_name

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, status) for status in statuses]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(result[0] for result in results) == ["conflict", "created"]
    assert next(value for state, value in results if state == "conflict") == "one_active_run"

    winner = next(value for state, value in results if state == "created")
    with postgres_engine.begin() as connection:
        connection.execute(update(runs).where(runs.c.id == winner).values(status="completed"))
    replacement = create_run(postgres_engine, subscription["id"])
    assert replacement["id"] != winner


def test_daily_deduplication_allows_other_days_and_manual_runs(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    subscription = subscriptions.save_user({"name": "每日去重"})
    other = subscriptions.save_user({"name": "其他订阅"})
    day = date(2026, 10, 26)
    create_run(postgres_engine, subscription["id"], status="completed", scheduled_date=day)
    with pytest.raises(IntegrityError) as error:
        create_run(postgres_engine, subscription["id"], status="completed", scheduled_date=day)
    assert error.value.orig.diag.constraint_name == "uq_runs_user_scheduled_date"

    create_run(postgres_engine, subscription["id"], status="completed", scheduled_date=date(2026, 10, 27))
    create_run(postgres_engine, other["id"], status="completed", scheduled_date=day)
    create_run(postgres_engine, subscription["id"], status="completed")
    create_run(postgres_engine, subscription["id"], status="completed")
    with postgres_engine.connect() as connection:
        assert len(connection.execute(select(runs.c.id)).all()) == 5


def test_parent_records_with_history_cannot_be_deleted(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    subscription = subscriptions.save_user({"name": "历史关联保护"})
    run = create_run(postgres_engine, subscription["id"], status="completed")
    with postgres_engine.begin() as connection:
        connection.execute(
            insert(traces).values(
                run_id=run["id"],
                tool="list_sources",
                arguments={},
                result="{}",
                status="ok",
                duration_ms=1,
                created_at=datetime.now(UTC),
            )
        )
    with pytest.raises(IntegrityError) as error:
        with postgres_engine.begin() as connection:
            connection.execute(delete(runs).where(runs.c.id == run["id"]))
    assert error.value.orig.sqlstate == "23503"
    with pytest.raises(IntegrityError) as error:
        with postgres_engine.begin() as connection:
            connection.execute(delete(users).where(users.c.id == subscription["id"]))
    assert error.value.orig.sqlstate == "23503"


def test_migration_can_roll_back_and_upgrade_without_losing_subscriptions(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    subscription = subscriptions.save_user({"name": "升级回退保留的订阅"})
    create_run(postgres_engine, subscription["id"], status="completed")
    with postgres_engine.begin() as connection:
        connection.execute(text("CREATE TABLE t02_subscription_demo (id INTEGER PRIMARY KEY)"))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "0001")
    tables = set(inspect(postgres_engine).get_table_names())
    assert tables == {"users", "alembic_version", "t02_subscription_demo"}
    assert subscriptions.user(subscription["id"]) == subscription
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.check(config)
    assert {"users", "runs", "traces", "notifications"} <= set(inspect(postgres_engine).get_table_names())
    assert subscriptions.user(subscription["id"]) == subscription
    with postgres_engine.connect() as connection:
        assert connection.execute(select(runs.c.id)).all() == []
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
