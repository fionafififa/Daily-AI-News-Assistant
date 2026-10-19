from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.schemas import Subscription
from app.storage import Store
from app.tools import Toolbox


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, scheduler_enabled=False)


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture
def user(store):
    return store.save_user(Subscription(name="测试工程师", excludes=["融资"], enabled=False).model_dump())


@pytest.fixture
def toolbox(settings, store, user):
    run = store.new_run(user["id"], "demo")
    return Toolbox(settings, store, run, user)


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
