import asyncio
import json
from datetime import datetime

import httpx
import pytest

from scripts import check_sources


@pytest.mark.parametrize(
    ("articles", "latest"),
    [
        ([], None),
        (
            [{"feed_entry_at": "2026-10-23T08:00:00+00:00", "published_at": None}],
            "2026-10-23T08:00:00+00:00",
        ),
        ([{"published_at": "2026-10-23T07:00:00+00:00"}], "2026-10-23T07:00:00+00:00"),
    ],
)
async def test_check_awaits_fetch_and_preserves_counts_and_date_basis(monkeypatch, articles, latest):
    class Client:
        async def fetch(self, source_id):
            assert source_id == "huggingface"
            return articles

    monkeypatch.setattr(check_sources, "NewsClient", Client)

    assert await check_sources.check("huggingface") == {
        "source": "huggingface",
        "status": "ok",
        "last_24h_count": len(articles),
        "latest": latest,
    }


async def test_check_reports_failure_instead_of_successful_empty_source(monkeypatch):
    class Client:
        async def fetch(self, source_id):
            raise httpx.ReadTimeout("Source timed out after all attempts")

    monkeypatch.setattr(check_sources, "NewsClient", Client)

    result = await check_sources.check("huggingface")

    assert result["source"] == "huggingface"
    assert result["status"] == "error"
    assert result["error"].startswith("ReadTimeout:")
    assert "last_24h_count" not in result


async def test_main_limits_concurrency_and_keeps_source_order_and_json_output(monkeypatch, capsys):
    source_ids = [f"source-{index}" for index in range(7)]
    active, peak = 0, 0
    started = []
    first_five_started = asyncio.Event()
    release = asyncio.Event()

    async def check(source_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(source_id)
        if len(started) == 5:
            first_five_started.set()
        try:
            await release.wait()
            if source_id == source_ids[2]:
                return {"source": source_id, "status": "error", "error": "Source unavailable"}
            return {"source": source_id, "status": "ok", "last_24h_count": 0, "latest": None}
        finally:
            active -= 1

    monkeypatch.setattr(check_sources, "SOURCES", dict.fromkeys(source_ids))
    monkeypatch.setattr(check_sources, "check", check)
    task = asyncio.create_task(check_sources.main())
    try:
        await asyncio.wait_for(first_five_started.wait(), timeout=2)
        assert len(started) == peak == 5
        release.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"checked_at", "sources"}
    assert datetime.fromisoformat(report["checked_at"]).utcoffset().total_seconds() == 0
    assert [source["source"] for source in report["sources"]] == source_ids
    assert report["sources"][2]["status"] == "error"
    assert all(source["status"] == "ok" for index, source in enumerate(report["sources"]) if index != 2)
    assert peak == 5 and active == 0
