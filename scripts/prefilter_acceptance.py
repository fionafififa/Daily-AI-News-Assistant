"""Frozen-input ON/OFF acceptance, capturing actual SDK HTTP request messages.

No real model, network search or push is used. The HTTP response script checks
integration, not the quality or cost of an LLM's editorial decisions.
Run: uv run --locked --group dev python -m scripts.prefilter_acceptance
"""

import argparse
import asyncio
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from langchain_openai import ChatOpenAI

from app.agent import run_live
from app.config import ROOT, Settings
from app.schemas import Subscription
from app.storage import Store, dumps
from app.tools import Toolbox

FIXTURE_PATH = ROOT / "tests/fixtures/prefilter.json"
QUOTE = "The release adds verified tool retry with a configurable retry budget."


def load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class FrozenNews:
    def __init__(self, fixture):
        self.fixture = fixture

    async def fetch(self, source_id):
        stamp = (datetime.fromisoformat(self.fixture["now"]) - timedelta(hours=1)).isoformat()
        return [
            {
                "source_id": source_id,
                "source": source_id,
                "published_at": stamp,
                "feed_entry_at": stamp,
                "evidence_kind": "feed_summary",
                "demo": False,
            }
            | copy.deepcopy(item)
            for item in self.fixture["feeds"][source_id]
        ]

    async def read(self, item):
        item["first_published_at"] = self.fixture["read"]["first_published_at"]
        return self.fixture["read"]["text"]


def build_box(fixture, data_dir, enabled=True, working_context=True):
    settings = Settings(
        mode="live",
        data_dir=data_dir,
        scheduler_enabled=False,
        summary_review_enabled=False,
        prefilter_enabled=enabled,
        working_context_enabled=working_context,
    )
    store = Store(data_dir)
    user = store.save_user(Subscription(**fixture["profile"]).model_dump())
    other = store.save_user(Subscription(name="其他用户", enabled=False).model_dump())
    for record in fixture["history"]:
        owner = user if record["owner"] == "self" else other
        run = store.new_run(owner["id"], record["mode"])
        store.update_run(
            run["id"],
            status="completed",
            brief={
                "title": "冻结历史简报",
                "generated_at": record["created_at"],
                "items": record["items"],
            },
        )
        with store.connect() as db:
            db.execute("UPDATE runs SET created_at=? WHERE id=?", (record["created_at"], run["id"]))
    run = store.new_run(user["id"], "live")
    return Toolbox(
        settings, store, run, user, FrozenNews(fixture), now=datetime.fromisoformat(fixture["now"])
    )


def actions():
    return [
        ("read_file", {"path": "profile.json"}),
        ("fetch_news", {"source_id": "huggingface"}),
        ("read_article", {"article_id": "cross-en"}),
        (
            "record_article_states",
            {
                "decisions": [
                    {
                        "article_id": "cross-en",
                        "state": "selected",
                        "reason": "与工具可靠性直接相关",
                        "evidence_quotes": [QUOTE],
                    }
                ]
            },
        ),
        ("fetch_news", {"source_id": "techcrunch"}),
        ("fetch_news", {"source_id": "huggingface"}),
        ("fetch_news", {"source_id": "arxiv"}),
        (
            "submit_brief",
            {
                "title": "冻结数据验收简报",
                "overview": "根据固定材料整理工具可靠性的技术进展。",
                "selections": [
                    {
                        "article_id": "cross-en",
                        "summary": "此版本提供可配置的工具重试预算，并明确可恢复失败的处理。",
                        "why_it_matters": "与你关注的工具可靠性直接相关。",
                        "topic": "智能体",
                        "relevance": 3,
                    }
                ],
            },
        ),
    ]


async def run_case(fixture, data_dir, enabled, working_context):
    box = build_box(fixture, data_dir, enabled, working_context)
    plan, received = actions(), []

    def handler(request):
        body = json.loads(request.content)
        received.append(body["messages"])
        index = len(received) - 1
        if index < len(plan):
            name, args = plan[index]
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
            message, finish = {"role": "assistant", "content": "已完成验收"}, "stop"
        return httpx.Response(
            200,
            json={
                "id": "frozen-acceptance",
                "object": "chat.completion",
                "created": 1,
                "model": "protocol-fixture",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as async_client:
        with httpx.Client(transport=transport) as sync_client:
            model = ChatOpenAI(
                model="protocol-fixture",
                api_key="test",
                base_url="https://model.example/v1",
                http_async_client=async_client,
                http_client=sync_client,
                max_retries=0,
            )
            brief = await run_live(box, model)

    archive = json.loads(box.material_archive_path.read_text(encoding="utf-8"))
    assert len(received) == len(plan) + 1
    assert brief["items"][0]["id"] == "cross-en"
    assert box.candidates["cross-en"]["text"] == fixture["read"]["text"]
    assert box.candidates["cross-en"]["first_published_at"] == fixture["read"]["first_published_at"]
    assert box.article_states["cross-en"]["state"] == "selected"
    assert box.article_reads["cross-en"] == "read"
    assert len(archive["prefilter_batches"]) == 4
    reasons = Counter()
    for batch in archive["prefilter_batches"]:
        reasons.update(batch["summary"]["reasons"])
        assert batch["summary"]["raw_count"] == len(batch["raw_articles"]) == len(batch["decisions"])
    if enabled:
        assert list(box.candidates) == fixture["expected"]["candidate_ids"]
        assert reasons == Counter(fixture["expected"]["first_pass_reasons"]) + Counter(
            {"duplicate_id": 5}
        ) + Counter(
            {
                "duplicate_url": 1,
                "history_id": 1,
                "history_url": 1,
                "history_title": 1,
                "excluded_title": 1,
            }
        )
    else:
        assert len(box.candidates) == 15
        assert not reasons

    request_metrics = []
    for index, messages in enumerate(received):
        text = dumps(messages)
        present = [marker for marker in fixture["expected"]["filtered_markers"] if marker in text]
        if enabled:
            assert not present, (index, present)
        snapshots = [
            json.loads(m["content"].split("\n", 1)[1])
            for m in messages
            if isinstance(m.get("content"), str) and m["content"].startswith("current_working_materials\n")
        ]
        if enabled:
            assert all(
                set(a["id"] for a in s["articles"]) <= set(fixture["expected"]["candidate_ids"])
                for s in snapshots
            )
        request_metrics.append(
            {
                "round": index + 1,
                "message_chars": len(text),
                "filtered_markers_present": present,
                "working_snapshots": len(snapshots),
            }
        )
    if not enabled:
        assert fixture["expected"]["filtered_markers"] == request_metrics[-1]["filtered_markers_present"]
    assert all(
        marker in dumps(archive["prefilter_batches"]) for marker in fixture["expected"]["filtered_markers"]
    )
    traces = box.store.traces(box.run["id"])
    assert all(trace["status"] == "ok" for trace in traces)
    assert [trace["tool"] for trace in traces] == [name for name, _ in plan]
    return {
        "prefilter_enabled": enabled,
        "working_context_enabled": working_context,
        "candidate_ids": list(box.candidates),
        "raw_count": sum(b["summary"]["raw_count"] for b in box.prefilter_batches),
        "kept_count": sum(b["summary"]["kept_count"] for b in box.prefilter_batches),
        "filter_reasons": dict(reasons),
        "batches": [{k: v for k, v in b.items() if k != "raw_articles"} for b in box.prefilter_batches],
        "requests": request_metrics,
        "message_chars_total": sum(r["message_chars"] for r in request_metrics),
        "preserved_read_text_dates_state": True,
        "all_original_entries_archived": True,
        "material_archive": archive,
        "trace_tools": [t["tool"] for t in traces],
        "brief_item_ids": [i["id"] for i in brief["items"]],
    }, received


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/prefilter-proof.json")
    parser.add_argument("--requests-output", type=Path, default=ROOT / "docs/prefilter-requests.json")
    args = parser.parse_args()
    fixture = load_fixture()
    results, requests = {}, {}
    with TemporaryDirectory(prefix="ai-news-prefilter-") as temp:
        for working_context in (True, False):
            for enabled in (True, False):
                key = f"prefilter_{'on' if enabled else 'off'}_context_{'on' if working_context else 'off'}"
                results[key], requests[key] = await run_case(
                    fixture, Path(temp) / key, enabled, working_context
                )
    args.requests_output.parent.mkdir(parents=True, exist_ok=True)
    args.requests_output.write_text(dumps(requests) + "\n", encoding="utf-8")
    proof = {
        "kind": "frozen-source-history-http-protocol-acceptance",
        "clock": fixture["now"],
        "fixture_sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "requests_sha256": hashlib.sha256(args.requests_output.read_bytes()).hexdigest(),
        "real_model_called": False,
        "api_usage": None,
        "measurement": "Characters of JSON-serialized HTTP messages; excludes tool schemas; not tokens or cost.",
        "cases": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        dumps(
            {
                "output": str(args.output),
                "requests_output": str(args.requests_output),
                "cases": {
                    key: {k: result[k] for k in ("raw_count", "kept_count", "message_chars_total")}
                    for key, result in results.items()
                },
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
