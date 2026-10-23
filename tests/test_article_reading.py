import asyncio
import json

import httpx
import pytest
from conftest import sample_article

from app.sources import NewsClient

ARTICLE_TEXT = (
    "The article describes an Agent framework with typed tools, explicit evidence, "
    "and observable execution. Readers can inspect the original experimental conditions "
    "before interpreting the reported results."
)
PAGE = f"""<html><head>
<meta property="article:published_time" content="2026-10-22T09:00:00Z">
</head><body><nav>Unrelated navigation</nav>
<article><p>{ARTICLE_TEXT}</p></article><footer>Unrelated footer</footer></body></html>"""


def article_tool(toolbox, handler, *, live=True):
    toolbox.run["mode"] = "live" if live else "demo"
    toolbox.client = NewsClient(transport=httpx.MockTransport(handler), retry_delay=0)
    item = sample_article()
    toolbox.candidates[item["id"]] = item
    tool = next(tool for tool in toolbox.tools if tool.name == "read_article")
    assert tool.func is None and tool.coroutine is not None
    return tool, item


async def test_article_recovers_after_timeout(toolbox):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) <= 3:
            # 第一次工具调用的三次尝试均超时，先降级到 RSS。
            raise httpx.ReadTimeout("模拟读取正文超时", request=request)
        else:
            # 第二次工具调用：正文恢复，旧警告应被清除。
            return httpx.Response(200, text=PAGE)

    tool, item = article_tool(toolbox, handler)
    first_result = await tool.ainvoke({"article_id": item["id"]})
    assert toolbox.article_reads[item["id"]] == "rss_only"
    assert "read_warning" in first_result
    assert len(requests) == 3
    second_result = await tool.ainvoke({"article_id": item["id"]})
    assert toolbox.article_reads[item["id"]] == "read"
    assert "read_warning" not in second_result
    assert second_result["text"] == ARTICLE_TEXT
    assert len(requests) == 4


async def test_article_timeout_is_retried_within_one_tool_call(toolbox):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("Temporary timeout", request=request)
        return httpx.Response(200, text=PAGE)

    tool, item = article_tool(toolbox, handler)
    result = await tool.ainvoke({"article_id": item["id"]})

    assert result["text"] == ARTICLE_TEXT
    assert toolbox.article_reads[item["id"]] == "read"
    assert "read_warning" not in result
    assert len(requests) == 2
    assert toolbox.calls == 1


async def test_article_reading_downloads_body_updates_evidence_and_records_trace(toolbox):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text=PAGE)

    tool, item = article_tool(toolbox, handler)
    item["read_warning"] = "Earlier attempt used RSS only"
    result = await tool.ainvoke({"article_id": item["id"]})

    assert requests == [item["url"]]
    assert result["text"] == ARTICLE_TEXT
    assert result["evidence_kind"] == "article"
    assert result["first_published_at"] == "2026-10-22T09:00:00+00:00"
    assert "read_warning" not in result
    assert toolbox.article_reads[item["id"]] == "read"
    assert toolbox.calls == 1
    trace = toolbox.store.traces(toolbox.run["id"])[-1]
    assert trace["tool"] == "read_article" and trace["status"] == "ok"
    assert json.loads(trace["result"])["text"] == ARTICLE_TEXT
    archive = json.loads(toolbox.material_archive_path.read_text(encoding="utf-8"))
    assert archive["articles"][item["id"]]["text"] == ARTICLE_TEXT
    assert archive["reads"][item["id"]] == "read"


async def test_already_read_article_reuses_body_without_another_request(toolbox):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text=PAGE)

    tool, item = article_tool(toolbox, handler)
    await tool.ainvoke({"article_id": item["id"]})
    result = await tool.ainvoke({"article_id": item["id"]})

    assert result["text"] == ARTICLE_TEXT
    assert requests == [item["url"]]


@pytest.mark.parametrize("failure", ["http_error", "timeout", "short_body"])
async def test_article_failure_preserves_rss_and_exposes_coverage_warning(toolbox, failure):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if failure == "timeout":
            raise httpx.ReadTimeout("Timed out reading article", request=request)
        if failure == "http_error":
            return httpx.Response(503, text="Unavailable")
        return httpx.Response(200, text="<article>Too short</article>")

    tool, item = article_tool(toolbox, handler)
    rss_text = item["text"]
    result = await tool.ainvoke({"article_id": item["id"]})

    assert requests == [item["url"]] * (3 if failure == "timeout" else 1)
    assert result["text"] == rss_text
    assert result["evidence_kind"] == "feed_summary"
    assert result["read_warning"]
    assert toolbox.article_reads[item["id"]] == "rss_only"
    trace = toolbox.store.traces(toolbox.run["id"])[-1]
    assert trace["tool"] == "read_article"
    assert json.loads(trace["result"])["read_warning"] == result["read_warning"]


async def test_article_cancellation_propagates_without_rss_fallback(toolbox):
    started = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200, text=PAGE)

    tool, item = article_tool(toolbox, handler)
    rss_text = item["text"]
    task = asyncio.create_task(tool.ainvoke({"article_id": item["id"]}))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(requests) == 1
        assert item["text"] == rss_text
        assert "read_warning" not in item
        assert toolbox.article_reads.get(item["id"]) != "rss_only"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_demo_reading_uses_existing_material_without_http(toolbox):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(503)

    tool, item = article_tool(toolbox, handler, live=False)
    original_text = item["text"]
    result = await tool.ainvoke({"article_id": item["id"]})

    assert result["text"] == original_text
    assert toolbox.article_reads[item["id"]] == "read"
    assert requests == []


async def test_unknown_article_id_is_reported_without_http(toolbox):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text=PAGE)

    tool, _ = article_tool(toolbox, handler)
    result = await tool.ainvoke({"article_id": "not-fetched"})

    assert "KeyError" in result["error"]
    assert requests == []
    assert toolbox.store.traces(toolbox.run["id"])[-1]["status"] == "error"
