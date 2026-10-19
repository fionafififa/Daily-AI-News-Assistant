"""Compare generation and summary-review usage within the same recorded runs."""

import argparse
import json
from pathlib import Path


def usage_for(responses, phase):
    selected = [row for row in responses if row.get("phase", "generation") == phase]
    usages = [row["usage"] for row in selected if row.get("usage")]
    result = {
        "calls": len(selected),
        "metered_calls": len(usages),
        "input_tokens": sum(row.get("input_tokens", 0) for row in usages),
        "output_tokens": sum(row.get("output_tokens", 0) for row in usages),
        "reasoning_output_tokens": sum(
            row.get("output_token_details", {}).get("reasoning", 0) for row in usages
        ),
        "total_tokens": sum(row.get("total_tokens", 0) for row in usages),
        "cache_read_tokens": sum(row.get("input_token_details", {}).get("cache_read", 0) for row in usages),
        "model_seconds": round(sum(row.get("duration_seconds", 0) for row in selected), 2),
    }
    result["uncached_input_tokens"] = result["input_tokens"] - result["cache_read_tokens"]
    return result


def compare(report):
    scenarios = []
    for scenario in report["scenarios"]:
        if scenario["status"] != "completed":
            continue
        generation = usage_for(scenario["model_observations"], "generation")
        review = usage_for(scenario["model_observations"], "summary_review")
        scenarios.append(
            {
                "scenario": scenario["scenario"],
                "status": scenario["status"],
                "items": len((scenario.get("brief") or {}).get("items", [])),
                "generation": generation,
                "summary_review": review,
                "with_review_tokens": generation["total_tokens"] + review["total_tokens"],
                "increment_percent": round(100 * review["total_tokens"] / generation["total_tokens"], 2)
                if generation["total_tokens"]
                else None,
                "elapsed_seconds": scenario["elapsed_seconds"],
            }
        )
    if not scenarios:
        raise ValueError("No completed runs available for a paired comparison")
    total = {
        phase: {key: round(sum(row[phase][key] for row in scenarios), 2) for key in scenarios[0][phase]}
        for phase in ("generation", "summary_review")
    }
    base = total["generation"]["total_tokens"]
    extra = total["summary_review"]["total_tokens"]
    return {
        "method": "Paired accounting of generation and review in the same runs; not two independent A/B executions. Total tokens include cached input and reasoning output. No price estimate.",
        "model": report["model"],
        "excluded_failed_runs": sum(row["status"] != "completed" for row in report["scenarios"]),
        "all_completed": all(row["status"] == "completed" for row in scenarios),
        "all_calls_metered": all(
            total[phase]["calls"] == total[phase]["metered_calls"]
            for phase in ("generation", "summary_review")
        ),
        "scenarios": scenarios,
        "totals": total,
        "with_review_tokens": base + extra,
        "increment_percent": round(100 * extra / base, 2) if base else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.report.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
