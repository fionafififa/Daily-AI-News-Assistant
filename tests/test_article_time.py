from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from app.article_time import date_labels, extract_dates, freshness_time
from app.delivery import render_email
from app.sources import NewsClient

PAGE = """<html><head><meta name="citation_date" content="2026/07/27"></head><body>
<main><blockquote class="abstract">Abstract: Agents use typed tools. Evaluation across nine models
and eight scenarios shows reduced overhead up to 38%, with substantial model variation.</blockquote>
<div class="submission-history">[v2] Fri, 16 Oct 2026 08:00:00 UTC<br>
[v1] Mon, 27 Jul 2026 03:38:01 UTC</div><section>Irrelevant footer and bibliography</section></main></body></html>"""


def test_extracts_first_submission_and_revision_without_overwriting_announcement():
    metadata = extract_dates(BeautifulSoup(PAGE, "html.parser"), "arxiv")
    assert metadata["first_published_at"] == "2026-07-27T03:38:01+00:00"
    assert metadata["published_at"] == metadata["first_published_at"]
    assert metadata["updated_at"] == "2026-10-16T08:00:00+00:00"
    assert "announced_at" not in metadata


def test_arxiv_recent_announcement_keeps_old_original_date_and_abstract_only():
    date = "Mon, 19 Oct 2026 04:00:00 GMT"
    rss = f"""<rss version="2.0"><channel><title>AI</title><item><title>Paper</title>
    <link>https://arxiv.org/abs/2610.22373</link><pubDate>{date}</pubDate>
    <description>A recently announced paper with an older first submission.</description></item></channel></rss>"""
    client = NewsClient(
        datetime(2026, 10, 19, 5, tzinfo=UTC),
        httpx.MockTransport(
            lambda request: httpx.Response(200, text=rss if request.url.host == "rss.arxiv.org" else PAGE)
        ),
    )
    item = client.fetch("arxiv")[0]
    assert item["published_at"] is None
    assert item["announced_at"] == "2026-10-19T04:00:00+00:00"
    text = client.read(item)
    assert item["first_published_at"].startswith("2026-07-27")
    assert freshness_time(item).startswith("2026-10-19")
    assert "Irrelevant" not in text
    assert "首次提交：2026-07-27" in date_labels(item)


def test_email_labels_announcement_and_first_submission_separately():
    item = {
        "source_id": "arxiv",
        "title": "Paper",
        "source": "arXiv",
        "summary": "摘要内容。",
        "why_it_matters": "匹配关注话题。",
        "url": "https://arxiv.org/abs/2610.22373",
        "announced_at": "2026-10-19T04:00:00+00:00",
        "first_published_at": "2026-07-27",
        "published_at": "2026-07-27",
    }
    plain, html = render_email(
        {"title": "简报", "overview": "来源整理。", "items": [item], "demo": False, "warnings": []}
    )
    assert "RSS 公告" in plain and "首次提交：2026-07-27" in plain
    assert "首次提交：2026-07-27" in html
