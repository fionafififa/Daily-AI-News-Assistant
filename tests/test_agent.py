"""Exercise create_agent + ChatOpenAI against a local mock HTTP provider, never a paid API."""

import json

import httpx
import pytest
from conftest import sample_article, selection
from langchain_openai import ChatOpenAI

from app.agent import run_live
from app.tools import Toolbox


@pytest.mark.parametrize(
    "plan",
    [
        ["read_file", "fetch_news", "read_article", "submit_bad", "submit_brief"],
        ["fetch_news", "write_file", "read_article", "read_file", "submit_brief"],
    ],
)
async def test_real_create_agent_follows_model_chosen_tools_and_recovers(settings, store, user, plan):
    settings.mode = "live"
    settings.summary_review_enabled = (
        False  # This test isolates Agent tool routing; review has separate tests.
    )
    run = store.new_run(user["id"], "live")

    class Client:
        def fetch(self, source_id):
            return [sample_article()]

        def read(self, article):
            return article["text"]

    box = Toolbox(settings, store, run, user, Client())
    received = []
    cursor = 0

    def handler(request):
        nonlocal cursor
        body = json.loads(request.content)
        received.append(body)
        registered = {t["function"]["name"] for t in body["tools"]}
        assert {"bash", "read_file", "submit_brief", "fetch_news"} <= registered
        assert body.get("tool_choice", "auto") == "auto"
        if cursor < len(plan):
            action = plan[cursor]
            args = {
                "read_file": {"path": "history.json"},
                "fetch_news": {"source_id": "huggingface"},
                "write_file": {"path": "scratch/research.md", "content": "model-selected note"},
                "read_article": {"article_id": "a1"},
                "submit_bad": {
                    "title": "测试简报",
                    "overview": "这是一份测试简报概述。",
                    "selections": [selection("invented")],
                },
                "submit_brief": {
                    "title": "测试简报",
                    "overview": "这是一份测试简报概述。",
                    "selections": [selection()],
                },
            }[action]
            name = "submit_brief" if action == "submit_bad" else action
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{cursor}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "已完成。"}
            finish = "stop"
        cursor += 1
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as async_client:
        with httpx.Client(transport=transport) as sync_client:
            model = ChatOpenAI(
                model="test-model",
                api_key="test-key",
                base_url="https://model.example/v1",
                http_async_client=async_client,
                http_client=sync_client,
                max_retries=0,
            )
            brief = await run_live(box, model=model)
    assert brief["items"][0]["id"] == "a1"
    expected = ["submit_brief" if name == "submit_bad" else name for name in plan]
    assert [r["tool"] for r in store.traces(run["id"])] == expected
    assert cursor == len(plan) + 1  # The model, not submit_brief, chose to stop.
    assert any(m["role"] == "tool" for m in received[-1]["messages"])
    if "submit_bad" in plan:
        assert any(
            "未知文章" in m.get("content", "") for m in received[-1]["messages"] if m["role"] == "tool"
        )
        assert store.traces(run["id"])[-2]["status"] == "error"


async def test_live_mode_missing_key_never_falls_back_to_fixtures(toolbox):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        await run_live(toolbox)
    assert not toolbox.candidates
