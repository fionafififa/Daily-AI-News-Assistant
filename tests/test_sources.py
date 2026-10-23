import asyncio
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest

from app.sources import NewsClient, article_id, canonical_url


async def test_rss_time_window_missing_dates_html_and_url_dedup():
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
    articles = await client.fetch("huggingface")
    assert len(articles) == 1
    assert articles[0]["text"] == "Actual facts."
    assert articles[0]["url"] == "https://huggingface.co/blog/new"


async def test_redirect_cannot_escape_source_allowlist():
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    client = NewsClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="允许域名"):
        await client.fetch("huggingface")
    assert len(requests) == 1


async def test_invalid_feed_is_failure_not_empty_news():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="<html>Blocked</html>")

    client = NewsClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="有效 RSS"):
        await client.fetch("huggingface")
    assert len(requests) == 1


async def test_valid_empty_feed_is_success_not_source_failure():
    xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
    client = NewsClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml)))

    assert await client.fetch("huggingface") == []


@pytest.mark.parametrize("timeout_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_timeout_propagates_after_three_attempts_without_becoming_empty_news(timeout_type):
    requests = []

    def handler(request):
        requests.append(request)
        raise timeout_type("Timed out receiving RSS", request=request)

    client = NewsClient(transport=httpx.MockTransport(handler), retry_delay=0)
    with pytest.raises(timeout_type):
        await client.fetch("huggingface")
    assert len(requests) == 3


@pytest.mark.parametrize("failures", [1, 2])
async def test_download_retries_timeout_and_can_succeed_on_last_attempt(failures):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) <= failures:
            raise httpx.ReadTimeout("Temporary timeout", request=request)
        return httpx.Response(200, content=b"complete response")

    client = NewsClient(transport=httpx.MockTransport(handler), retry_delay=0)
    result = await client.download("https://huggingface.co/feed", ["huggingface.co"])

    assert result == b"complete response"
    assert len(requests) == failures + 1


async def test_retry_restarts_redirect_chain_from_original_url():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/feed":
            return httpx.Response(302, headers={"location": "/body"})
        if len(paths) == 2:
            raise httpx.ReadTimeout("Timeout after redirect", request=request)
        return httpx.Response(200, content=b"complete response")

    client = NewsClient(transport=httpx.MockTransport(handler), retry_delay=0)

    assert await client.download("https://huggingface.co/feed", ["huggingface.co"]) == b"complete response"
    assert paths == ["/feed", "/body", "/feed", "/body"]


async def test_redirect_limit_is_failure_without_retry():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "/loop"})

    client = NewsClient(transport=httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="重定向次数过多"):
        await client.download("https://huggingface.co/feed", ["huggingface.co"])
    assert len(requests) == 4


@pytest.mark.parametrize("status_code", [404, 503])
async def test_http_error_preserves_status_without_becoming_empty_news(status_code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, text="Unavailable")

    client = NewsClient(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await client.fetch("huggingface")
    assert caught.value.response.status_code == status_code
    assert len(requests) == 1


async def test_timeout_retry_stops_when_next_attempt_returns_http_error():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("Temporary timeout", request=request)
        return httpx.Response(404, text="Not found")

    client = NewsClient(transport=httpx.MockTransport(handler), retry_delay=0)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        await client.download("https://huggingface.co/feed", ["huggingface.co"])
    assert caught.value.response.status_code == 404
    assert len(requests) == 2


class TrackedTransport(httpx.MockTransport):
    def __init__(self, handler):
        super().__init__(handler)
        self.closed = False

    async def aclose(self):
        self.closed = True
        await super().aclose()


class TrackedBody(httpx.AsyncByteStream):
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error
        self.closed = False

    async def __aiter__(self):
        yield self.payload
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed = True


class WaitingBody(TrackedBody):
    def __init__(self):
        super().__init__(b"partial response")
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        yield self.payload
        self.started.set()
        await self.release.wait()


async def test_download_closes_response_after_reading_body():
    body = TrackedBody(b"complete response")
    client = NewsClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=body)))

    assert await client.download("https://huggingface.co/blog/feed.xml", ["huggingface.co"]) == body.payload
    assert body.closed


async def test_interrupted_body_is_read_failure_and_closes_response():
    requests = []
    body = TrackedBody(b"<rss><channel>", error=httpx.ReadError("Connection lost during response body"))

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=body)

    client = NewsClient(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.ReadError):
        await client.fetch("huggingface")
    assert body.closed
    assert len(requests) == 1


async def test_body_timeout_closes_failed_response_and_discards_partial_content():
    bodies = [
        TrackedBody(b"discard this partial response", error=httpx.ReadTimeout("Body timeout")),
        TrackedBody(b"complete response"),
    ]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=bodies[len(requests) - 1])

    client = NewsClient(transport=httpx.MockTransport(handler), retry_delay=0)

    assert await client.download("https://huggingface.co/feed", ["huggingface.co"]) == b"complete response"
    assert len(requests) == 2
    assert all(body.closed for body in bodies)


async def test_oversized_body_is_rejected_and_response_is_closed():
    requests = []
    body = TrackedBody(b"x" * 2_000_001)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=body)

    client = NewsClient(transport=httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="2 MB"):
        await client.fetch("huggingface")
    assert body.closed
    assert len(requests) == 1


@pytest.mark.parametrize("phase", ["body", "backoff"])
async def test_download_deadline_stops_body_or_retry_wait_and_closes_resources(phase):
    requests = []
    body = WaitingBody()

    def handler(request):
        requests.append(request)
        if phase == "backoff":
            raise httpx.ReadTimeout("Temporary timeout", request=request)
        return httpx.Response(200, stream=body)

    transport = TrackedTransport(handler)
    client = NewsClient(transport=transport, download_timeout=0.05, retry_delay=10)
    task = asyncio.create_task(client.download("https://huggingface.co/feed", ["huggingface.co"]))
    try:
        # The outer guard makes a missing deadline fail rather than hang the test.
        done, _ = await asyncio.wait({task}, timeout=2)
        assert task in done, "Download did not stop at its deadline"
        with pytest.raises(TimeoutError):
            await task
        assert len(requests) == 1
        assert transport.closed
        if phase == "body":
            assert body.closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("phase", ["body", "backoff"])
async def test_cancellation_stops_download_without_retry_and_closes_resources(phase):
    requests = []
    body = WaitingBody()
    timed_out = asyncio.Event()

    def handler(request):
        requests.append(request)
        if phase == "backoff":
            timed_out.set()
            raise httpx.ReadTimeout("Temporary timeout", request=request)
        return httpx.Response(200, stream=body)

    transport = TrackedTransport(handler)
    client = NewsClient(transport=transport, retry_delay=10)
    task = asyncio.create_task(client.download("https://huggingface.co/feed", ["huggingface.co"]))
    try:
        started = body.started if phase == "body" else timed_out
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(requests) == 1
        assert transport.closed
        if phase == "body":
            assert body.closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_url_normalization_preserves_meaningful_query():
    assert canonical_url("https://example.com/a/?id=3&utm_source=x#top") == "https://example.com/a?id=3"
    assert article_id("https://example.com/a?utm_source=1") == article_id("https://example.com/a")
