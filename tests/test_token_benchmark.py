import pytest

from scripts.benchmark_tokens import SnapshotNews, comparisons, usage_total


def test_usage_keeps_cache_in_input_and_reasoning_inside_output_without_double_counting():
    calls = [
        {
            "phase": "generation",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 80,
                "prompt_tokens_details": {"cached_tokens": 80},
                "completion_tokens_details": {"reasoning_tokens": 7},
            },
        },
        {
            "phase": "summary_review",
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 5,
                "total_tokens": 35,
                "prompt_tokens_details": {"cached_tokens": 10},
            },
        },
        {"phase": "generation", "http_status": 503},
    ]
    generation = usage_total(calls, "generation")
    assert generation["input_tokens"] == 100 and generation["output_tokens"] == 20
    assert generation["cache_read_tokens"] == 80 and generation["uncached_input_tokens"] == 20
    assert generation["reasoning_output_tokens"] == 7 and generation["total_tokens"] == 120
    assert generation["attempted_calls"] == 2 and generation["unmetered_attempts"] == 1
    assert usage_total(calls)["total_tokens"] == 155


def test_comparison_requires_completed_runs_and_complete_usage_and_allows_negative_savings():
    def arm(name, status, count, missing=0):
        usage = {
            "input_tokens": count,
            "output_tokens": count,
            "total_tokens": 2 * count,
            "unmetered_attempts": missing,
        }
        return {
            "arm": name,
            "status": status,
            "generation": usage,
            "summary_review": usage,
            "all_phases": usage,
        }

    baseline = arm("baseline", "completed", 100)
    changed = arm("context_only", "completed", 120)
    assert comparisons([baseline, changed])[0]["all_phases"]["total_tokens_reduction_percent"] == -20
    for invalid in (arm("context_only", "failed", 5), arm("context_only", "completed", 5, 1)):
        row = comparisons([baseline, invalid])[0]
        assert not row["completed_metered_pair"]
        assert row["all_phases"]["total_tokens_reduction_percent"] is None


async def test_frozen_news_never_mutates_original_and_missing_body_is_explicit():
    fixture = {"feeds": {"arxiv": [{"id": "paper", "text": "original feed"}]}, "read_cache": {}}
    client = SnapshotNews(fixture)
    receipt = await client.fetch("arxiv")
    receipt[0]["text"] = "mutated by a tool"
    receipt = await client.fetch("arxiv")
    assert receipt[0]["text"] == "original feed"
    with pytest.raises(OSError, match="RSS evidence"):
        await client.read({"id": "paper"})
