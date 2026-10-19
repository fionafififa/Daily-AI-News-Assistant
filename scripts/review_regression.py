"""Real-model regression controls using captured public evidence, not new news items."""

import argparse
import asyncio
import copy
import json
from pathlib import Path

from langchain_openai import ChatOpenAI

from app.config import Settings
from app.summary_review import review_brief
from scripts.live_acceptance import ModelObservations


async def main(captured, output):
    settings = Settings.load()
    old = json.loads(captured.read_text(encoding="utf-8"))["scenarios"][0]
    original = next(item for item in old["brief"]["items"] if item["id"] == "434246a93fec15537b14")
    evidence = next(item for item in old["selected_evidence"] if item["id"] == original["id"])
    cases = [
        ("historical_bad_score", original["summary"]),
        (
            "correct_score_control",
            "在 480 个公共记录任务中，作者报告经规则和检查支持的平均得分从 30.1 提高到最高实测 59.2/100。",
        ),
        (
            "unsupported_guarantee_control",
            "论文证明该系统在所有生产任务上都保证 100% 准确率，无需任何前提条件。",
        ),
    ]
    results = []
    for name, summary in cases:
        item = copy.deepcopy(original)
        item["summary"] = summary
        brief = {"overview": "根据论文摘要整理的实验结果。", "items": [item]}
        observations = ModelObservations()
        model = ChatOpenAI(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=45,
            max_retries=1,
            callbacks=[observations],
        )
        try:
            checked = await review_brief(brief, {evidence["id"]: evidence}, model)
            revised = checked["items"][0]["summary"]
            if name == "historical_bad_score":
                passed = "59.2" in revised and "100" in revised and "达到满分" not in revised
            elif name == "correct_score_control":
                passed = (
                    revised == summary and checked["summary_review"]["items"][0]["verdict"] == "supported"
                )
            else:
                verdict = checked["summary_review"]["items"][0]["verdict"]
                claim_removed = "所有生产任务上都保证" not in revised and "无需任何前提" not in revised
                claim_negated = any(
                    marker in revised for marker in ("并不表示", "不代表", "不能保证", "不能推广")
                )
                passed = verdict == "corrected" and (claim_removed or claim_negated)
            result = {
                "case": name,
                "passed": passed,
                "outcome": "reviewed",
                "original_summary": summary,
                "checked": checked,
                "observations": observations.responses,
            }
        except ValueError as exc:
            result = {
                "case": name,
                "passed": name == "unsupported_guarantee_control" and "证据不足" in str(exc),
                "outcome": "rejected",
                "error": str(exc),
                "observations": observations.responses,
            }
        results.append(result)
        output.write_text(
            json.dumps({"model": settings.model, "cases": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {"case": name, "passed": result["passed"], "outcome": result["outcome"]}, ensure_ascii=False
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captured", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.captured, args.output))
