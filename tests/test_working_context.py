import copy
import json

import httpx
import pytest
from conftest import sample_article, selection
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from app.agent import run_live
from app.schemas import ArticleDecision, Selection
from app.storage import dumps
from app.tools import Toolbox
from app.working_context import message_record, rebuild_messages, working_materials

QUOTE = "The release adds verified tool retry with a configurable retry budget."


class News:
    async def fetch(self, source_id):
        return [
            sample_article(f"a{i:02d}") | {"text": f"FEED_ONLY_{i:02d} " + "source material " * 300}
            for i in range(1, 41)
        ]

    async def read(self, article):
        return QUOTE + " ORIGINAL_BODY_ONLY " + "verified original material " * 300


def decide(key, state, reason="与用户关注的话题直接相关", quotes=None):
    return ArticleDecision(article_id=key, state=state, reason=reason, evidence_quotes=quotes or [])


def context_text(messages):
    return dumps([message_record(message) for message in messages])


async def test_rebuild_removes_excluded_text_keeps_pending_evidence_dates_and_full_archive(toolbox):
    toolbox.client = News()
    toolbox.run["mode"] = "live"
    fetched = await toolbox.fetch_news("huggingface")
    original = copy.deepcopy(fetched)
    read = copy.deepcopy(await toolbox.read_article("a01"))
    toolbox.candidates["a01"]["first_published_at"] = "2026-07-27"
    toolbox.candidates["a01"]["announced_at"] = "2026-10-19T04:00:00+00:00"
    toolbox.record_article_states(
        [decide("a01", "selected", quotes=[QUOTE]), decide("a02", "excluded", "融资报道，与订阅无关")]
    )
    messages = [
        HumanMessage(content="保留用户原始任务与约束"),
        AIMessage(content="", tool_calls=[{"id": "fetch", "name": "fetch_news", "args": {}}]),
        ToolMessage(content=dumps(original), name="fetch_news", tool_call_id="fetch"),
        AIMessage(content="", tool_calls=[{"id": "read", "name": "read_article", "args": {}}]),
        ToolMessage(content=dumps(read), name="read_article", tool_call_id="read"),
        AIMessage(content="", tool_calls=[{"id": "bad", "name": "read_article", "args": {}}]),
        ToolMessage(content=dumps({"error": "unknown article"}), name="read_article", tool_call_id="bad"),
    ]
    before = context_text(messages)
    rebuilt = rebuild_messages(messages, toolbox)
    after = context_text(rebuilt)
    assert "FEED_ONLY_02" not in after
    assert "融资报道，与订阅无关" in after
    assert "FEED_ONLY_03" in after  # No decision: never silently drop it.
    assert QUOTE in after and "ORIGINAL_BODY_ONLY" not in after
    assert "2026-07-27" in after and "2026-10-19T04:00:00+00:00" in after
    assert "unknown article" in after
    assert context_text(messages) == before  # Full original messages remain intact.
    assert [m.tool_call_id for m in rebuilt if isinstance(m, ToolMessage)] == ["fetch", "read", "bad"]
    archived = json.loads(toolbox.material_archive_path.read_text(encoding="utf-8"))
    assert "FEED_ONLY_02" in archived["articles"]["a02"]["text"]
    assert "ORIGINAL_BODY_ONLY" in archived["articles"]["a01"]["text"]
    assert archived["states"]["a02"]["state"] == "excluded"


async def test_state_batch_validation_is_atomic_and_rejects_invented_evidence(toolbox):
    toolbox.run["mode"] = "live"
    toolbox.client = News()
    await toolbox.fetch_news("huggingface")
    for decisions in (
        [decide("a01", "excluded"), decide("missing", "excluded")],
        [decide("a01", "selected")],
        [decide("a01", "selected", quotes=["invented source quotation"])],
        [decide("a01", "excluded"), decide("a01", "to_read")],
    ):
        with pytest.raises(ValueError):
            toolbox.record_article_states(decisions)
        assert toolbox.article_states["a01"]["state"] == "pending"


@pytest.mark.parametrize("state", ["selected", "excluded"])
async def test_reread_reopens_material_and_refetch_does_not_destroy_evidence(toolbox, state):
    toolbox.run["mode"] = "live"
    toolbox.client = News()
    await toolbox.fetch_news("huggingface")
    await toolbox.read_article("a01")
    toolbox.record_article_states([decide("a01", state, quotes=[QUOTE] if state == "selected" else [])])
    await toolbox.fetch_news("huggingface")
    assert toolbox.candidates["a01"]["text"].startswith(QUOTE)
    assert toolbox.article_states["a01"]["state"] == state
    await toolbox.read_article("a01")
    assert toolbox.article_states["a01"]["state"] == "to_read"
    assert "ORIGINAL_BODY_ONLY" in working_materials(toolbox)["articles"][0]["text"]


async def test_submit_requires_selected_state_when_rebuilding_is_enabled(toolbox):
    toolbox.run["mode"] = "live"
    toolbox.client = News()
    await toolbox.fetch_news("huggingface")
    with pytest.raises(ValueError, match="入选理由"):
        toolbox.submit_brief("测试简报", "符合用户偏好的更新内容。", [Selection(**selection("a01"))])
    await toolbox.read_article("a01")
    toolbox.record_article_states([decide("a01", "selected", quotes=[QUOTE])])
    assert toolbox.submit_brief("测试简报", "符合用户偏好的更新内容。", [Selection(**selection("a01"))])[
        "accepted"
    ]


def validate_tool_pairs(messages):
    pending = set()
    for message in messages:
        if message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
        else:
            assert not pending, "Non-tool message interrupted a tool call/result group"
            if message["role"] == "assistant":
                pending = {call["id"] for call in message.get("tool_calls", [])}
    assert not pending


@pytest.mark.parametrize("read_first", [False, True])
async def test_actual_http_requests_follow_state_and_model_selected_order(settings, store, user, read_first):
    settings.mode = "live"
    settings.summary_review_enabled = False
    box = Toolbox(settings, store, store.new_run(user["id"], "live"), user, News())
    exclusion = {"decisions": [decide("a02", "excluded", "融资报道").model_dump()]}
    selected = {"decisions": [decide("a01", "selected", quotes=[QUOTE]).model_dump()]}
    actions = [
        ("fetch_news", {"source_id": "huggingface"}),
        ("read_article", {"article_id": "a01"}) if read_first else ("record_article_states", exclusion),
        ("record_article_states", exclusion) if read_first else ("read_article", {"article_id": "a01"}),
        (
            "record_article_states",
            {"decisions": [decide("a01", "selected", quotes=["forged evidence"]).model_dump()]},
        ),
        ("record_article_states", selected),
        (
            "submit_brief",
            {
                "title": "测试简报",
                "overview": "符合用户偏好的 Agent 更新。",
                "selections": [selection("a01")],
            },
        ),
    ]
    received = []

    def handler(request):
        body = json.loads(request.content)
        validate_tool_pairs(body["messages"])
        received.append(body)
        index = len(received) - 1
        if index < len(actions):
            name, args = actions[index]
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": name, "arguments": dumps(args)},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "已完成"}
            finish = "stop"
        return httpx.Response(
            200,
            json={
                "id": "test",
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
                api_key="test",
                base_url="https://model.example/v1",
                http_async_client=async_client,
                http_client=sync_client,
                max_retries=0,
            )
            brief = await run_live(box, model)
    assert brief["items"][0]["id"] == "a01"
    assert len(received) == len(actions) + 1  # Model still decides to stop.
    assert "FEED_ONLY_02" in dumps(received[1]["messages"])
    removal_round = 3 if read_first else 2
    for request in received[removal_round:]:
        text = dumps(request["messages"])
        assert "FEED_ONLY_02" not in text and "融资报道" in text
        assert (
            sum(
                m.get("content", "").startswith("current_working_materials\n")
                for m in request["messages"]
                if isinstance(m.get("content"), str)
            )
            == 1
        )
    final = dumps(received[-1]["messages"])
    assert QUOTE in final and "ORIGINAL_BODY_ONLY" not in final
    assert "连续片段" in final  # Tool error survived rebuilding.
    assert "FEED_ONLY_03" in final  # Unreviewed candidate survives.
    assert len(box.context_observations) == len(received)
    assert store.traces(box.run["id"])[3]["status"] == "error"
    archive = json.loads(box.material_archive_path.read_text(encoding="utf-8"))
    assert "FEED_ONLY_02" in archive["articles"]["a02"]["text"]


async def test_forty_article_materials_shrink_after_explicit_decisions(toolbox):
    toolbox.run["mode"] = "live"
    toolbox.client = News()
    fetched = copy.deepcopy(await toolbox.fetch_news("huggingface"))
    messages = [
        HumanMessage(content="整理每日新闻"),
        AIMessage(content="", tool_calls=[{"id": "fetch", "name": "fetch_news", "args": {}}]),
        ToolMessage(content=dumps(fetched), name="fetch_news", tool_call_id="fetch"),
    ]
    before = context_text(rebuild_messages(messages, toolbox))
    await toolbox.read_article("a01")
    toolbox.record_article_states(
        [decide("a01", "selected", quotes=[QUOTE]), decide("a02", "to_read", "需要阅读原文")]
        + [decide(f"a{i:02d}", "excluded", "与用户订阅无关") for i in range(3, 41)]
    )
    after = context_text(rebuild_messages(messages, toolbox))
    assert len(after) < len(before) * 0.1
    assert QUOTE in after and "FEED_ONLY_02" in after
    assert "FEED_ONLY_03" not in after
    archived = json.loads(toolbox.material_archive_path.read_text(encoding="utf-8"))
    assert "FEED_ONLY_01" in archived["feed_articles"]["a01"]["text"]
    assert "ORIGINAL_BODY_ONLY" in archived["articles"]["a01"]["text"]
