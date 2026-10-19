from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest

from app.sources import NewsClient, article_id, canonical_url


def test_rss_time_window_missing_dates_html_and_url_dedup():
    now = datetime(2026, 10, 18, 8, tzinfo=UTC)
    date = format_datetime(datetime(2026, 10, 18, 7, tzinfo=UTC))
    xml = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
    <item><title>New model</title><link>https://huggingface.co/blog/new?utm_source=rss</link>
    <pubDate>{date}</pubDate><description><![CDATA[<p>Actual facts.</p><script>evil()</script>]]></description></item>
    <item><title>Duplicate</title><link>https://huggingface.co/blog/new</link><pubDate>{date}</pubDate></item>
    <item><title>Old</title><link>https://huggingface.co/blog/old</link><pubDate>Mon, 12 Oct 2026 07:00:00 GMT</pubDate></item>
    <item><title>Undated</title><link>https://huggingface.co/blog/undated</link></item>
    <item><title>Private</title><link>https://127.0.0.1/private</link><pubDate>{date}</pubDate></item>
    </channel></rss>"""
    client = NewsClient(now, httpx.MockTransport(lambda request: httpx.Response(200, text=xml)))
    articles = client.fetch("huggingface")
    assert len(articles) == 1
    assert articles[0]["text"] == "Actual facts."
    assert articles[0]["url"] == "https://huggingface.co/blog/new"


def test_redirect_cannot_escape_source_allowlist():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    client = NewsClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="允许域名"):
        client.fetch("huggingface")
    assert len(requests) == 1


def test_invalid_feed_is_failure_not_empty_news():
    client = NewsClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>Blocked</html>"))
    )
    with pytest.raises(ValueError, match="有效 RSS"):
        client.fetch("huggingface")


def test_url_normalization_preserves_meaningful_query():
    assert canonical_url("https://example.com/a/?id=3&utm_source=x#top") == "https://example.com/a?id=3"
    assert article_id("https://example.com/a?utm_source=1") == article_id("https://example.com/a")
