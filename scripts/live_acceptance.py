"""Opt-in live acceptance: real configured model + real RSS, inbox delivery only.

Run from the project root. Credentials are read from an explicit dotenv file and
never copied into the report. This script makes at most three complete Agent runs.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

from app.agent import run_live
from app.config import ROOT, Settings
from app.schemas import Subscription
from app.service import NewsService
from app.storage import Store


class ModelObservations(BaseCallbackHandler):
    """Record timings, usage and chosen tool names, without model reasoning text."""

    def __init__(self):
        self.starts = {}
        self.responses = []
        self.phases = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.starts[str(run_id)] = time.monotonic()
        self.phases[str(run_id)] = (kwargs.get("metadata") or {}).get("phase", "generation")

    def on_llm_end(self, response, *, run_id, **kwargs):
        message = response.generations[0][0].message
        self.responses.append(
            {
                "phase": self.phases.pop(str(run_id), "generation"),
                "duration_seconds": round(
                    time.monotonic() - self.starts.pop(str(run_id), time.monotonic()), 2
                ),
                "tool_names": [call["name"] for call in getattr(message, "tool_calls", [])],
                "usage": getattr(message, "usage_metadata", None),
                "finish_reason": message.response_metadata.get("finish_reason"),
            }
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.starts.pop(str(run_id), None)
        self.responses.append(
            {"error_type": type(error).__name__, "phase": self.phases.pop(str(run_id), "generation")}
        )


async def accept(settings, output_dir, max_scenarios, resume_path=None):
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    previous = json.loads(resume_path.read_text(encoding="utf-8")) if resume_path else None
    if previous:
        if previous["model"] != settings.model or Path(previous["project_root"]) != ROOT:
            raise ValueError("续跑必须使用相同项目与模型。")
        for name, expected_hash in previous["source_hashes"].items():
            if hashlib.sha256((ROOT / "app" / name).read_bytes()).hexdigest() != expected_hash:
                raise ValueError("应用源码已变化，请启动新的验收，避免混合不同版本结果。")
        data_dir = Path(previous["data_dir"])
        if not data_dir.resolve().is_relative_to((settings.data_dir / "acceptance").resolve()):
            raise ValueError("续跑数据目录不属于当前项目的验收目录。")
    else:
        data_dir = settings.data_dir / "acceptance" / f"{stamp}-{uuid4().hex[:6]}"
    settings = replace(
        settings,
        mode="live",
        scheduler_enabled=False,
        data_dir=data_dir,
    )
    store = Store(settings.data_dir)
    boxes = {}
    observations = ModelObservations()

    async def runner(box):
        boxes[box.run["id"]] = box
        model = ChatOpenAI(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=45,
            max_retries=1,
            callbacks=[observations],
        )
        return await run_live(box, model=model)

    service = NewsService(settings, store, live_runner=runner)
    existing = {user["name"]: user for user in store.users()}
    engineer = existing.get("真实验收·工程师") or store.save_user(
        Subscription(
            name="真实验收·工程师",
            role="后端工程师",
            topics=["AI 编程", "Agent", "开源模型", "AI 产品"],
            keywords=["工具调用", "本地部署"],
            excludes=["融资", "营销活动"],
            enabled=False,
            channel="inbox",
            max_items=5,
        ).model_dump()
    )
    researcher = existing.get("真实验收·研究者") or store.save_user(
        Subscription(
            name="真实验收·研究者",
            role="机器学习研究者",
            topics=["研究论文", "模型评测", "机器学习"],
            keywords=["benchmark", "evaluation"],
            excludes=["融资"],
            enabled=False,
            channel="inbox",
            max_items=3,
        ).model_dump()
    )
    scenarios = [
        ("engineer_initial", engineer),
        ("engineer_history", engineer),
        ("researcher_initial", researcher),
    ][:max_scenarios]
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = resume_path or output_dir / f"live-acceptance-{stamp}.json"
    report = previous or {
        "started_at": datetime.now(UTC).isoformat(),
        "project_root": str(ROOT),
        "source_hashes": {
            name: hashlib.sha256((ROOT / "app" / name).read_bytes()).hexdigest()
            for name in (
                "agent.py",
                "tools.py",
                "sources.py",
                "service.py",
                "config.py",
                "summary_review.py",
                "article_time.py",
            )
        },
        "dependencies": {name: version(name) for name in ("langchain", "langchain-openai", "fastapi")},
        "model": settings.model,
        "mode": "live",
        "delivery": "inbox",
        "data_dir": str(settings.data_dir),
        "semantic_review": "pending",
        "scenarios": [],
    }
    prior_first = next((s for s in report["scenarios"] if s["scenario"] == "engineer_initial"), None)
    first_ids = (
        {item["id"] for item in prior_first["brief"]["items"]}
        if prior_first and prior_first["brief"]
        else set()
    )
    completed_names = {s["scenario"] for s in report["scenarios"] if s["status"] == "completed"}
    try:
        for name, user in scenarios:
            if name in completed_names:
                continue
            print(json.dumps({"event": "scenario_start", "scenario": name}, ensure_ascii=False), flush=True)
            start = time.monotonic()
            model_start = len(observations.responses)
            run = service.enqueue(user["id"])
            await asyncio.gather(*list(service.tasks))
            finished = store.run(run["id"])
            box = boxes.get(run["id"])
            traces = store.traces(run["id"])
            brief = finished["brief"]
            items = brief["items"] if brief else []
            ids = {item["id"] for item in items}
            draft = getattr(box, "pre_review", None)
            evidence_ids = ids or {item["id"] for item in (draft or {}).get("items", [])}
            checked = {
                "completed": finished["status"] == "completed",
                "inbox_delivered": finished["delivery_status"] == "sent"
                and bool(store.notifications(user["id"])),
                "real_model_responses": len(observations.responses) > model_start,
                "no_demo_items": brief is not None
                and not brief["demo"]
                and all(not item.get("demo") and not item["id"].startswith("demo-") for item in items),
                "within_item_limit": len(items) <= user["max_items"],
                "selected_articles_were_fetched": box is not None and ids <= set(box.candidates),
                "working_sources_checked": bool(brief and brief["sources_checked"]),
            }
            if name == "engineer_history":
                checked["history_ids_not_repeated"] = not (ids & first_ids)
            if name == "engineer_initial":
                first_ids = ids
            result = {
                "scenario": name,
                "run_id": run["id"],
                "elapsed_seconds": round(time.monotonic() - start, 2),
                "status": finished["status"],
                "error": finished["error"],
                "profile": user,
                "checks": checked,
                "brief": brief,
                "tool_sequence": [row["tool"] for row in traces],
                "traces": traces,
                "model_observations": observations.responses[model_start:],
                "candidate_count": len(box.candidates) if box else 0,
                "selected_evidence": [box.candidates[key] for key in sorted(evidence_ids)] if box else [],
                "pre_review_brief": draft,
                "review_audit": getattr(box, "review_audit", None),
                "content_review": "pending" if items else "not_exercised_empty_brief",
            }
            report["scenarios"].append(result)
            report["updated_at"] = datetime.now(UTC).isoformat()
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "event": "scenario_end",
                        "scenario": name,
                        "status": finished["status"],
                        "articles": len(items),
                        "tools": len(traces),
                        "checks": checked,
                        "elapsed_seconds": result["elapsed_seconds"],
                        "report": str(report_path),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if finished["status"] != "completed":
                break  # Diagnose before spending more model calls.
    finally:
        await service.close()
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs")
    parser.add_argument("--scenarios", type=int, choices=[1, 2, 3], default=3)
    parser.add_argument(
        "--resume", type=Path, help="Continue remaining scenarios in an existing report/database"
    )
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error(f"找不到配置文件：{args.env_file}。请保存 .env 或用 --env-file 指定路径。")
    load_dotenv(args.env_file, override=True)
    settings = Settings.load()
    if not settings.api_key:
        parser.error("配置文件中缺少 OPENAI_API_KEY。")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(accept(settings, args.output_dir.resolve(), args.scenarios, args.resume))


if __name__ == "__main__":
    main()
