import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.config import Settings
from app.schemas import Subscription
from app.storage import create_store
from app.tools import Toolbox


@pytest.fixture
def settings(tmp_path, request):
    database_url = ""
    if getattr(request, "param", "sqlite") == "postgresql":
        database_url = request.getfixturevalue("postgres_engine").url.render_as_string(hide_password=False)
    return Settings(data_dir=tmp_path, scheduler_enabled=False, database_url=database_url)


@pytest.fixture
def store(settings):
    store = create_store(settings)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def user(store):
    return store.save_user(Subscription(name="测试工程师", excludes=["融资"], enabled=False).model_dump())


@pytest.fixture
def toolbox(settings, store, user):
    run = store.new_run(user["id"], "demo")
    return Toolbox(settings, store, run, user)


@pytest.fixture
def postgres_engine(monkeypatch):
    """Migrate an isolated database, never writing tests into the user's database."""
    raw_url = os.getenv("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("设置 TEST_POSTGRES_URL 后运行真实 PostgreSQL 验收")
    base_url = make_url(raw_url)
    if base_url.drivername != "postgresql+psycopg":
        pytest.fail("TEST_POSTGRES_URL 必须使用 postgresql+psycopg")

    database_name = "t02_subscriptions_test_" + uuid4().hex[:12]
    assert re.fullmatch(r"t02_subscriptions_test_[0-9a-f]{12}", database_name)
    assert database_name != base_url.database
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        created = True
        test_url = base_url.set(database=database_name)
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        project_root = Path(__file__).resolve().parents[1]
        command.upgrade(Config(str(project_root / "alembic.ini")), "head")
        engine = create_engine(test_url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                assert re.fullmatch(r"t02_subscriptions_test_[0-9a-f]{12}", database_name)
                with admin.connect() as connection:
                    connection.exec_driver_sql(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        finally:
            admin.dispose()


def sample_article(key="a1", title="Test Agent tool release"):
    return {
        "id": key,
        "title": title,
        "url": f"https://huggingface.co/blog/{key}",
        "published_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "source_id": "huggingface",
        "source": "Hugging Face",
        "demo": False,
        "text": "This is a test fixture about an Agent framework release with tool retry and error handling.",
        "evidence_kind": "feed_summary",
    }


def selection(article_id="a1"):
    return {
        "article_id": article_id,
        "summary": "测试文章介绍工具调用失败后的重试行为，并描述适用条件。",
        "why_it_matters": "与你关注的 Agent 工程实践直接相关。",
        "topic": "Agent",
        "relevance": 3,
    }
