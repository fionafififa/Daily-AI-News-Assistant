"""Paid, real-model three-arm comparison using one frozen real-news snapshot.

No mock model responses, forced tool sequence or delivery. Persist each provider
usage response immediately, including failures, without headers or reasoning.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

from app.agent import run_live
from app.config import ROOT, Settings
from app.prefilter import HistoryIndex
from app.schemas import Subscription
from app.sources import SOURCES
from app.storage import Store, dumps
from app.tools import Toolbox

ARMS = {"baseline": (False, False), "context_only": (False, True), "prefilter_and_context": (True, True)}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(report_path, fixture_path):
    captured = json.loads(report_path.read_text(encoding="utf-8"))
    scenario = next(
        s for s in captured["scenarios"] if s["status"] == "completed" and s.get("material_archive_path")
    )
    archive_path = Path(scenario["material_archive_path"])
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    feeds = {key: [] for key in SOURCES}
    for item in archive["feed_articles"].values():
        feeds[item["source_id"]].append(item)
    fixture = {
        "provenance": {
            "report": report_path.name,
            "report_sha256": sha256(report_path),
            "archive_sha256": sha256(archive_path),
            "captured_at": scenario["brief"]["generated_at"],
        },
        "now": (datetime.fromisoformat(scenario["brief"]["generated_at"]) + timedelta(minutes=1)).isoformat(),
        "profile": {k: v for k, v in scenario["profile"].items() if k not in {"id", "email"}},
        "history": [scenario["brief"]],
        "feeds": feeds,
        "read_cache": {
            key: archive["articles"][key] for key, state in archive["reads"].items() if state == "read"
        },
        "read_policy": "Frozen verified originals when available; otherwise read_article reports RSS fallback. Never fetch online.",
    }
    fixture["profile"].update(name="Token 对照验收工程师", enabled=False, channel="inbox")
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return fixture


class SnapshotNews:
    def __init__(self, fixture):
        self.fixture = fixture

    async def fetch(self, source_id):
        return copy.deepcopy(self.fixture["feeds"][source_id])

    async def read(self, article):
        cached = self.fixture["read_cache"].get(article["id"])
        if cached is None:
            raise OSError("No verified original in frozen snapshot; use labelled RSS evidence")
        for key in (
            "published_at",
            "first_published_at",
            "announced_at",
            "updated_at",
            "publication_kind",
            "evidence_kind",
        ):
            if key in cached:
                article[key] = cached[key]
        return cached["text"]


def usage_total(calls, phase=None):
    selected = [c for c in calls if phase is None or c["phase"] == phase]
    metered = [c["usage"] for c in selected if c.get("usage")]
    result = {
        "attempted_calls": len(selected),
        "metered_calls": len(metered),
        "unmetered_attempts": len(selected) - len(metered),
        "input_tokens": sum(u["prompt_tokens"] for u in metered),
        "output_tokens": sum(u["completion_tokens"] for u in metered),
        "total_tokens": sum(u["total_tokens"] for u in metered),
        "cache_read_tokens": sum(
            u.get("prompt_cache_hit_tokens", (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
            for u in metered
        ),
        "reasoning_output_tokens": sum(
            (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) for u in metered
        ),
    }
    result["uncached_input_tokens"] = result["input_tokens"] - result["cache_read_tokens"]
    return result


class Meter(BaseCallbackHandler):
    def __init__(self, directory):
        self.directory = directory
        self.phase = "generation"
        self.calls = []

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.phase = (kwargs.get("metadata") or {}).get("phase", "generation")

    async def request(self, request):
        body = json.loads(request.content)
        snapshots = [
            json.loads(m["content"].split("\n", 1)[1])
            for m in body["messages"]
            if isinstance(m.get("content"), str) and m["content"].startswith("current_working_materials\n")
        ]
        row = {
            "call": len(self.calls) + 1,
            "phase": self.phase,
            "started": time.monotonic(),
            "message_chars": len(dumps(body["messages"])),
            "working_snapshots": len(snapshots),
            "state_counts": snapshots[0]["counts"] if snapshots else {},
            "excluded_text_absent": all(
                "text" not in a and "evidence_quotes" not in a
                for s in snapshots
                for a in s["articles"]
                if a["state"] == "excluded"
            ),
        }
        self.calls.append(row)
        with (self.directory / "requests.jsonl").open("a", encoding="utf-8") as stream:
            # No Authorization headers, response text or model reasoning.
            stream.write(
                dumps({"call": row["call"], "phase": self.phase, "messages": body["messages"]}) + "\n"
            )
        self.save()

    async def response(self, response):
        await response.aread()
        row = self.calls[-1]
        row["http_status"] = response.status_code
        row["elapsed_seconds"] = round(time.monotonic() - row["started"], 2)
        if response.is_success:
            payload = response.json()
            row.update(usage=payload.get("usage"), response_id=payload.get("id"), model=payload.get("model"))
            row["tool_names"] = [
                t["function"]["name"]
                for c in payload.get("choices", [])
                for t in c.get("message", {}).get("tool_calls", [])
            ]
            row["finish_reasons"] = [c.get("finish_reason") for c in payload.get("choices", [])]
        self.save()
        print(
            dumps(
                {
                    "event": "model_response",
                    "arm": self.directory.name,
                    "call": row["call"],
                    "phase": row["phase"],
                    "http_status": row["http_status"],
                    "usage": row.get("usage"),
                    "tools": row.get("tool_names", []),
                }
            ),
            flush=True,
        )

    def save(self):
        (self.directory / "calls.json").write_text(
            json.dumps(self.calls, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def build_box(settings, fixture):
    store = Store(settings.data_dir)
    user = store.save_user(Subscription(**fixture["profile"]).model_dump())
    for brief in fixture["history"]:
        run = store.new_run(user["id"], "live")
        store.update_run(run["id"], status="completed", brief=brief)
        with store.connect() as db:
            db.execute("UPDATE runs SET created_at=? WHERE id=?", (brief["generated_at"], run["id"]))
    run = store.new_run(user["id"], "live")
    return Toolbox(
        settings, store, run, user, SnapshotNews(fixture), now=datetime.fromisoformat(fixture["now"])
    )


async def run_arm(settings, fixture, name, directory):
    prefilter, context = ARMS[name]
    directory.mkdir(parents=True, exist_ok=False)
    settings = replace(
        settings,
        mode="live",
        scheduler_enabled=False,
        data_dir=directory / "data",
        prefilter_enabled=prefilter,
        working_context_enabled=context,
    )
    box, meter = build_box(settings, fixture), Meter(directory)
    started = time.monotonic()
    brief, error = None, None
    print(dumps({"event": "arm_start", "arm": name}), flush=True)
    async with httpx.AsyncClient(
        event_hooks={"request": [meter.request], "response": [meter.response]}
    ) as client:
        model = ChatOpenAI(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=90,
            max_retries=0,
            http_async_client=client,
            callbacks=[meter],
        )
        try:
            brief = await asyncio.wait_for(run_live(box, model), settings.agent_timeout)
        except Exception as exc:
            # Only local validation errors are safe to quote. SDK errors can contain URLs or headers.
            error = str(exc)[:500] if isinstance(exc, ValueError) else type(exc).__name__
        finally:
            box.active = False
            meter.save()
    ids = {a["id"] for a in (brief or {}).get("items", [])}
    history = HistoryIndex(fixture["history"])
    traces = box.store.traces(box.run["id"])
    result = {
        "arm": name,
        "prefilter_enabled": prefilter,
        "working_context_enabled": context,
        "status": "completed" if brief is not None else "failed",
        "error": error,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "brief": brief,
        "pre_review_brief": getattr(box, "pre_review", None),
        "review_audit": getattr(box, "review_audit", None),
        "candidate_count": len(box.candidates),
        "candidate_ids": list(box.candidates),
        "states": dict(Counter(d["state"] for d in box.article_states.values())),
        "sources_checked": sorted(box.successful_sources),
        "read_status": box.article_reads,
        "tool_sequence": [t["tool"] for t in traces],
        "tool_errors": [t for t in traces if t["status"] != "ok"],
        "calls": meter.calls,
        "generation": usage_total(meter.calls, "generation"),
        "summary_review": usage_total(meter.calls, "summary_review"),
        "all_phases": usage_total(meter.calls),
        "checks": {
            "nonempty_brief": bool(ids),
            "within_limit": len(ids) <= box.user["max_items"],
            "no_history_duplicates": all(not history.match(box.candidates[k]) for k in ids),
            "no_demo": all(not box.candidates[k].get("demo") for k in ids),
            "all_successful_calls_metered": all(
                c.get("usage") for c in meter.calls if c.get("http_status") == 200
            ),
            "excluded_text_absent": all(c["excluded_text_absent"] for c in meter.calls),
        },
        "material_archive_path": str(box.material_archive_path.relative_to(ROOT)),
        "request_archive_path": str((directory / "requests.jsonl").relative_to(ROOT)),
    }
    (directory / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        dumps(
            {
                "event": "arm_end",
                "arm": name,
                "status": result["status"],
                "error": error,
                "generation": result["generation"],
                "summary_review": result["summary_review"],
            }
        ),
        flush=True,
    )
    return result


def comparisons(results):
    by_arm = {r["arm"]: r for r in results}
    rows = []
    for base, changed in (
        ("baseline", "context_only"),
        ("context_only", "prefilter_and_context"),
        ("baseline", "prefilter_and_context"),
    ):
        if base not in by_arm or changed not in by_arm:
            continue
        a, b = by_arm[base], by_arm[changed]
        valid = a["status"] == b["status"] == "completed" and not any(
            r["all_phases"]["unmetered_attempts"] for r in (a, b)
        )
        row = {"base": base, "changed": changed, "completed_metered_pair": valid}
        for phase in ("generation", "summary_review", "all_phases"):
            row[phase] = {
                key + "_reduction_percent": round(100 * (1 - b[phase][key] / a[phase][key]), 2)
                if valid and a[phase][key]
                else None
                for key in ("input_tokens", "output_tokens", "total_tokens")
            }
        rows.append(row)
    return rows


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captured", type=Path, default=ROOT / "docs/live-acceptance-20261019T130235Z.json")
    parser.add_argument("--fixture", type=Path, default=ROOT / "docs/token-benchmark-fixture-2026-10-20.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/token-benchmark-2026-10-20.json")
    parser.add_argument("--arms", nargs="+", choices=list(ARMS), default=list(ARMS))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    if not settings.api_key:
        raise ValueError("Configured model API key is required")
    hashes = {p.name: sha256(p) for p in sorted((ROOT / "app").glob("*.py"))}
    if args.resume:
        report = json.loads(args.output.read_text(encoding="utf-8"))
        if (
            report["source_hashes"] != hashes
            or report["model"] != settings.model
            or report["fixture_sha256"] != sha256(args.fixture)
        ):
            raise ValueError("Resume requires unchanged source, model and frozen fixture")
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    else:
        if args.output.exists():
            raise ValueError("Output already exists; use --resume or a new --output")
        fixture = freeze(args.captured, args.fixture)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report = {
            "started_at": datetime.now(UTC).isoformat(),
            "model": settings.model,
            "method": "Independent real-model agent executions; same frozen real feeds, history, read cache, clock and tool budget. One sample per arm; model chooses all tools. No delivery.",
            "fixture_sha256": sha256(args.fixture),
            "source_hashes": hashes,
            "script_sha256": sha256(Path(__file__)),
            "dependencies": {key: version(key) for key in ("langchain", "langchain-openai", "httpx")},
            "agent_timeout": max(settings.agent_timeout, 300),
            "max_tool_calls": settings.max_tool_calls,
            "summary_review_enabled": settings.summary_review_enabled,
            "data_dir": str((settings.data_dir / "acceptance" / f"tokens-{stamp}").relative_to(ROOT)),
            "results": [],
        }
    settings = replace(settings, agent_timeout=report["agent_timeout"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in args.arms:
        if any(r["arm"] == name for r in report["results"]):
            continue  # Never discard failed runs or silently select a cheaper rerun.
        result = await run_arm(settings, fixture, name, ROOT / report["data_dir"] / name)
        report["results"].append(result)
        report["comparisons"] = comparisons(report["results"])
        report["updated_at"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(dumps({"report": str(args.output), "comparisons": report.get("comparisons", [])}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
