import json

import pytest
from langchain_core.messages import AIMessage

from app.summary_review import review_brief, verify_numeric_claims

SOURCE = "On 480 tasks, the score rose from 30.1 to the maximum of 59.2 out of 100."


class Reviewer:
    def __init__(self, result):
        self.result = result
        self.messages = None

    def bind(self, **kwargs):
        assert kwargs == {"response_format": {"type": "json_object"}}
        return self

    async def ainvoke(self, messages, **kwargs):
        assert kwargs["config"]["metadata"]["phase"] == "summary_review"
        self.messages = messages
        return AIMessage(content=json.dumps(self.result))


def decision(verdict="corrected", corrected="在 480 个任务上，平均得分从 30.1 提高至最高实测 59.2/100。"):
    return {
        "overview": "本次简报依据已读取的来源整理。",
        "items": [
            {
                "article_id": "a1",
                "verdict": verdict,
                "corrected_summary": corrected,
                "reason": "分母为 100，59.2 是最高实测分数。",
                "evidence_quotes": ["maximum of 59.2 out of 100"],
            }
        ],
    }


def inputs(summary="原得分 30.1（满分 59.2），改进后达到满分。"):
    return {"overview": "实验结果测试简报。", "items": [{"id": "a1", "summary": summary}]}, {
        "a1": {"id": "a1", "text": SOURCE, "first_published_at": "2026-10-15"}
    }


async def test_review_repairs_score_semantics_preserves_original_and_uses_bounded_context():
    brief, sources = inputs()
    sources["a1"]["text"] += " x" * 20000
    model = Reviewer(decision())
    checked = await review_brief(brief, sources, model, source_chars=1000)
    assert "59.2/100" in checked["items"][0]["summary"]
    assert "满分" in brief["items"][0]["summary"]
    assert checked["summary_review"]["status"] == "checked"
    payload = json.loads(model.messages[-1]["content"])
    assert len(payload["articles"][0]["source_text"]) == 1000
    assert "history" not in payload


@pytest.mark.parametrize(
    "summary", ["平均得分提高，最终达到满分。", "原得分 30.1（满分 59.2）。", "最终得分达 99.9 分。"]
)
def test_deterministic_numeric_backstop_rejects_bad_claims(summary):
    with pytest.raises(ValueError):
        verify_numeric_claims(summary, SOURCE)


def test_number_translation_and_correct_denominator_are_supported():
    verify_numeric_claims(
        "9 个模型、3 类任务，最高得分 59.2/100。", "Nine models, three tasks, maximum of 59.2 out of 100."
    )


async def test_unsupported_or_fabricated_evidence_is_not_published():
    brief, sources = inputs()
    rejected = decision("reject", None)
    with pytest.raises(ValueError, match="证据不足"):
        await review_brief(brief, sources, Reviewer(rejected))
    forged = decision()
    forged["items"][0]["evidence_quotes"] = ["The system achieved perfect accuracy."]
    with pytest.raises(ValueError, match="原文连续片段"):
        await review_brief(brief, sources, Reviewer(forged))


@pytest.mark.parametrize(
    "published", ["2026-07-27", "2026-07-27T03:38:01+00:00", "2026-10-15T15:26:54+00:00"]
)
async def test_verified_date_metadata_can_support_summary_date_numbers(published):
    brief, sources = inputs(f"{published[:10]} 提交；实验最高实测 59.2/100。")
    sources["a1"]["first_published_at"] = published
    checked = await review_brief(brief, sources, Reviewer(decision("supported", None)))
    assert checked["summary_review"]["status"] == "checked"


def test_dates_leading_zeroes_and_reported_none_are_normalized():
    verify_numeric_claims("7 月 27 日提交；0 个错误。", "2026-07-27. reported none.")


def test_numeric_units_and_model_names_do_not_hide_scientific_values():
    verify_numeric_claims(
        "0.62M 参数模型，速度提升 6.8 倍，吞吐提升 3.2 倍。Qwen3 与 Phi-4。",
        "A 0.62M-parameter model. Up to 6.8x fewer passes and 3.2x throughput. Qwen3 and Phi-4.",
    )
    with pytest.raises(ValueError, match="9.8"):
        verify_numeric_claims("速度提升 9.8 倍。", "Up to 6.8x fewer passes.")


async def test_reviewer_cannot_silently_drop_items_or_approve_wrong_score():
    brief, sources = inputs()
    missing = decision()
    missing["items"] = []
    with pytest.raises(ValueError, match="缺少条目"):
        await review_brief(brief, sources, Reviewer(missing))
    with pytest.raises(ValueError, match="满分"):
        await review_brief(brief, sources, Reviewer(decision("supported", None)))


async def test_empty_brief_requires_no_model_call():
    checked = await review_brief({"items": []}, {}, None)
    assert checked["summary_review"]["model_calls"] == 0
