import copy
import json
from collections import Counter

import pytest
from conftest import selection

from app.prefilter import explicit_title_exclusion
from app.schemas import ArticleDecision, Selection
from scripts.prefilter_acceptance import QUOTE, build_box, load_fixture, run_case


async def test_first_pass_exact_candidates_counts_history_scope_and_full_audit(tmp_path):
    fixture = load_fixture()
    box = build_box(fixture, tmp_path)
    receipts = [await box.fetch_news(source) for source in fixture["feeds"]]
    assert [(r["raw_count"], r["count"]) for r in receipts] == [(10, 5), (4, 2), (1, 1)]
    assert list(box.candidates) == fixture["expected"]["candidate_ids"]
    reasons = Counter()
    for receipt in receipts:
        reasons.update(receipt["prefilter"]["reasons"])
        assert receipt["count"] == receipt["prefilter"]["kept_count"]
    assert reasons == fixture["expected"]["first_pass_reasons"]
    archive = json.loads(box.material_archive_path.read_text(encoding="utf-8"))
    for source, batch in zip(fixture["feeds"], archive["prefilter_batches"], strict=True):
        for raw, original in zip(batch["raw_articles"], fixture["feeds"][source], strict=True):
            assert all(raw[key] == value for key, value in original.items())
        assert len(batch["decisions"]) == len(batch["raw_articles"])
    decisions = {d["id"]: d for b in archive["prefilter_batches"] for d in b["decisions"]}
    assert decisions["history-url-new-id"]["duplicate_of"] == "old-url-id"
    assert decisions["excluded-en"]["matched_term"] == "Funding"
    assert len(box.history) == 1  # same user, same mode, last seven days only
    assert "cross-en" in box.candidates  # Chinese preference / English article; other user's history
    assert "no-keyword" in box.candidates  # no positive keyword whitelist; demo history ignored
    assert "out-of-window" in box.candidates  # expired history ignored
    assert "ambiguous-body" in box.candidates  # body-only exclusion mention is undecided
    assert "same-title-new-url" in box.candidates  # no current-run title gate


@pytest.mark.parametrize(
    ("title", "terms", "expected"),
    [
        ("Startup FUNDING announced", ["Funding"], "Funding"),
        ("Funding-backed startup", ["funding"], "funding"),
        ("公司完成融资", ["融资"], "融资"),
        ("OpenAI releases a model", ["AI"], None),
        ("Said runtime is available", ["AI"], None),
        ("Crowdfunding infrastructure", ["Funding"], None),
        ("New runtime", ["融资", "Funding"], None),
        ("Runtime C++ release", ["C++"], "C++"),
        ("New runtime", ["", "  "], None),
    ],
)
def test_title_gate_is_literal_conservative_and_escapes_regex(title, terms, expected):
    assert explicit_title_exclusion(title, terms) == expected


@pytest.mark.parametrize("state", ["selected", "excluded"])
async def test_cross_source_and_repeat_fetch_preserve_verified_material_and_decisions(tmp_path, state):
    fixture = load_fixture()
    box = build_box(fixture, tmp_path)
    await box.fetch_news("huggingface")
    await box.read_article("cross-en")
    box.record_article_states(
        [
            ArticleDecision(
                article_id="cross-en",
                state=state,
                reason="记录已完成的编辑判断",
                evidence_quotes=[QUOTE] if state == "selected" else [],
            )
        ]
    )
    original, decision = (
        copy.deepcopy(box.candidates["cross-en"]),
        copy.deepcopy(box.article_states["cross-en"]),
    )
    receipt = await box.fetch_news("techcrunch")
    assert receipt["count"] == 2
    fixture["feeds"]["huggingface"][0]["text"] = "REFETCH_CHANGED_RSS Original shorter feed text changed."
    receipt = await box.fetch_news("huggingface")
    assert receipt["count"] == 0
    assert box.candidates["cross-en"] == original
    assert box.article_states["cross-en"] == decision
    assert box.article_reads["cross-en"] == "read"
    archive = json.loads(box.material_archive_path.read_text(encoding="utf-8"))
    assert archive["prefilter_batches"][0]["raw_articles"][0]["text"].startswith("KEEP_ENGLISH")
    assert archive["prefilter_batches"][2]["raw_articles"][0]["text"].startswith("REFETCH_CHANGED_RSS")
    assert archive["articles"]["cross-en"] == original


@pytest.mark.parametrize("article_id", ["history-id", "history-url-new-id", "history-title-new-id"])
async def test_disabled_prefilter_still_has_final_history_guard(tmp_path, article_id):
    box = build_box(load_fixture(), tmp_path, enabled=False, working_context=False)
    receipt = await box.fetch_news("huggingface")
    assert receipt["count"] == 10
    with pytest.raises(ValueError, match="近期已推送"):
        box.submit_brief(
            "最终校验", "即使关闭预筛选也不允许推送重复历史。", [Selection(**selection(article_id))]
        )


async def test_disabled_prefilter_still_rejects_duplicate_urls_and_exclusions_at_submit(tmp_path):
    box = build_box(load_fixture(), tmp_path, enabled=False, working_context=False)
    await box.fetch_news("huggingface")
    with pytest.raises(ValueError, match="重复新闻"):
        box.submit_brief(
            "最终校验",
            "关闭预筛选仍须校验重复链接。",
            [
                Selection(**selection("cross-en")),
                Selection(**selection("same-feed-duplicate")),
            ],
        )
    with pytest.raises(ValueError, match="排除词"):
        box.submit_brief("最终校验", "关闭预筛选仍须校验排除词。", [Selection(**selection("excluded-zh"))])


async def test_arxiv_old_first_submission_with_fresh_announcement_can_be_submitted(tmp_path):
    fixture = load_fixture()
    box = build_box(fixture, tmp_path)
    receipt = await box.fetch_news("arxiv")
    assert receipt["count"] == 1
    item = box.candidates["arxiv-announced"]
    box.record_article_states(
        [
            ArticleDecision(
                article_id=item["id"],
                state="selected",
                reason="当天公告，首次提交时间单独标注",
                evidence_quotes=[
                    "This paper was first submitted earlier but was announced in today's RSS feed."
                ],
            )
        ]
    )
    assert box.submit_brief(
        "论文日期验收",
        "按当天公告纳入，保留较早的首次提交日期。",
        [
            Selection(**selection(item["id"])),
        ],
    )["accepted"]
    assert box.accepted["items"][0]["first_published_at"] == "2026-10-01T12:00:00+00:00"
    assert box.accepted["items"][0]["announced_at"] == "2026-10-20T02:00:00+00:00"


async def test_all_filtered_is_successful_coverage_and_can_produce_empty_brief(tmp_path):
    fixture = load_fixture()
    fixture["feeds"]["huggingface"] = [fixture["feeds"]["huggingface"][5]]
    box = build_box(fixture, tmp_path)
    receipt = await box.fetch_news("huggingface")
    assert receipt["raw_count"] == 1 and receipt["count"] == 0
    assert receipt["prefilter"]["reasons"] == {"excluded_title": 1}
    assert box.successful_sources == {"huggingface"} and not box.source_errors
    assert box.submit_brief("空简报验收", "成功获取来源，内容均命中明确的过滤条件。", [])["accepted"]


@pytest.mark.parametrize("working_context", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
async def test_actual_http_materials_on_off_independent_of_context_rebuild(
    tmp_path, enabled, working_context
):
    result, requests = await run_case(load_fixture(), tmp_path, enabled, working_context)
    assert result["raw_count"] == 25
    assert result["kept_count"] == (8 if enabled else 25)
    # First request containing fetched news (round 3), plus every later request.
    assert len(requests) == 9
    for observation in result["requests"][2:]:
        if enabled:
            assert not observation["filtered_markers_present"]
    assert result["requests"][2]["working_snapshots"] == int(working_context)
