"""Opt-in PostgreSQL checks; each test migrates a separate disposable database."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db_schema import users
from app.postgres_subscriptions import PostgresSubscriptions
from app.schemas import Subscription


def test_create_is_committed_and_json_profile_round_trips(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    profile = Subscription(name="O'Brien 的 AI 简报", topics=["Agent", "Agent"], enabled=False)
    saved = subscriptions.save_user(profile.model_dump())
    assert len(saved["id"]) == 32
    UUID(saved["id"])
    assert saved == profile.model_dump() | {"id": saved["id"]}
    assert subscriptions.user(saved["id"]) == saved

    second_engine = create_engine(postgres_engine.url)
    try:
        with second_engine.connect() as connection:
            row = connection.execute(select(users).where(users.c.id == saved["id"])).mappings().one()
            assert row["profile"] == profile.model_dump()
            assert isinstance(row["profile"], dict)
            assert row["created_at"].utcoffset() is not None
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002"
    finally:
        second_engine.dispose()


def test_update_preserves_identifier_creation_time_and_other_subscriptions(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    first = subscriptions.save_user({"name": "原订阅"})
    other = subscriptions.save_user({"name": "其他订阅"})
    with postgres_engine.connect() as connection:
        created_at = connection.execute(
            select(users.c.created_at).where(users.c.id == first["id"])
        ).scalar_one()

    updated = subscriptions.save_user({"name": "研究简报", "topics": ["RAG"]}, first["id"])
    assert updated["id"] == first["id"]
    assert updated["name"] == "研究简报"
    assert updated["topics"] == ["RAG"]
    assert subscriptions.user(first["id"]) == updated
    assert subscriptions.user(other["id"]) == other
    assert len(subscriptions.users()) == 2
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(select(users.c.created_at).where(users.c.id == first["id"])).scalar_one()
            == created_at
        )


def test_unknown_read_returns_none_and_unknown_update_does_not_create(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    assert subscriptions.user("missing") is None
    assert subscriptions.users() == []
    with pytest.raises(KeyError) as error:
        subscriptions.save_user({"name": "不存在的订阅"}, "missing")
    assert error.value.args == ("missing",)
    assert subscriptions.users() == []


@pytest.mark.parametrize(
    "profile",
    [
        {"name": ""},
        {"name": "无偏好", "topics": [], "keywords": []},
        {"name": "不能指定内部编号", "id": "injected"},
    ],
)
def test_invalid_profile_is_rejected_without_writing(postgres_engine, profile):
    subscriptions = PostgresSubscriptions(postgres_engine)
    with pytest.raises(ValidationError):
        subscriptions.save_user(profile)
    assert subscriptions.users() == []


def test_integrity_error_rolls_back_and_next_write_can_succeed(postgres_engine, monkeypatch):
    subscriptions = PostgresSubscriptions(postgres_engine)
    first = subscriptions.save_user({"name": "保留的订阅"})
    with monkeypatch.context() as patch:
        patch.setattr("app.postgres_subscriptions.uuid4", lambda: UUID(first["id"]))
        with pytest.raises(IntegrityError):
            subscriptions.save_user({"name": "重复主键不能覆盖旧数据"})
    assert subscriptions.users() == [first]
    second = subscriptions.save_user({"name": "故障后仍可保存"})
    assert second["id"] != first["id"]
    assert len(subscriptions.users()) == 2


def test_list_orders_by_creation_time_then_identifier(postgres_engine):
    subscriptions = PostgresSubscriptions(postgres_engine)
    first = subscriptions.save_user({"name": "较晚创建"})
    second = subscriptions.save_user({"name": "较早创建"})
    now = datetime.now(UTC)
    with postgres_engine.begin() as connection:
        connection.execute(update(users).where(users.c.id == first["id"]).values(created_at=now))
        connection.execute(
            update(users).where(users.c.id == second["id"]).values(created_at=now - timedelta(days=1))
        )
    assert subscriptions.users() == [second, first]
    with postgres_engine.begin() as connection:
        connection.execute(update(users).values(created_at=now))
    assert [item["id"] for item in subscriptions.users()] == sorted([first["id"], second["id"]])
