"""Small RSS adapters. No credentials, arbitrary URLs, or paywall bypasses."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from .article_time import extract_dates

SOURCES = {
    "openai": {
        "name": "OpenAI News",
        "url": "https://openai.com/news/rss.xml",
        "domains": ["openai.com"],
        "topics": "模型、AI 编程、Agent、产品",
    },
    "google": {
        "name": "Google AI",
        "url": "https://blog.google/technology/ai/rss/",
        "domains": ["blog.google", "deepmind.google", "developers.googleblog.com"],
        "topics": "模型、多模态、AI 应用、研究",
    },
    "huggingface": {
        "name": "Hugging Face",
        "url": "https://huggingface.co/blog/feed.xml",
        "domains": ["huggingface.co"],
        "topics": "开源模型、本地部署、工程实践",
    },
    "techcrunch": {
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "domains": ["techcrunch.com"],
        "topics": "AI 产品、行业、融资",
    },
    "arxiv": {
        "name": "arXiv cs.AI / cs.LG",
        "url": "https://rss.arxiv.org/rss/cs.AI+cs.LG",
        "domains": ["rss.arxiv.org", "arxiv.org", "export.arxiv.org"],
        "topics": "论文、机器学习、研究（仅研究偏好时使用）",
    },
}


def clean_text(value):
    soup = BeautifulSoup(value or "", "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        node.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Unsupported article URL")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def article_id(url):
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:20]


def normalized_title(title):
    return re.sub(r"[^\w]", "", title.casefold())


def allowed_url(url, domains):
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname in domains
        and not parts.username
        and not parts.password
        and parts.port in {None, 443}
    )


class NewsClient:
    def __init__(self, now=None, transport=None):
        self.now = now or datetime.now(UTC)
        self.transport = transport

    def download(self, url, domains):
        # Re-check every redirect; RSS links cannot cause requests to localhost or arbitrary hosts.
        with httpx.Client(
            timeout=12,
            follow_redirects=False,
            transport=self.transport,
            headers={"User-Agent": "DailyAINewsSample/0.1 (RSS reader)"},
        ) as client:
            for _ in range(4):
                if not allowed_url(url, domains):
                    raise ValueError("来源链接不在允许域名内")
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["location"])
                        continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ValueError("来源响应超过 2 MB")
                        chunks.append(chunk)
                    return b"".join(chunks)
            raise ValueError("来源重定向次数过多")

    def fetch(self, source_id):
        source = SOURCES[source_id]
        payload = self.download(source["url"], source["domains"])
        parsed = feedparser.parse(payload)
        if not parsed.version:
            raise ValueError("返回内容不是有效 RSS/Atom")
        cutoff = self.now - timedelta(hours=24)
        articles, seen = [], set()
        for entry in parsed.entries[:200]:
            stamp = entry.get("published_parsed") or entry.get("updated_parsed")
            if not stamp:
                continue
            published = datetime(*stamp[:6], tzinfo=UTC)
            if not cutoff <= published <= self.now + timedelta(minutes=5):
                continue
            try:
                url = canonical_url(entry.get("link") or entry.get("id", ""))
                # Some official arXiv feeds still supply HTTP links; only upgrade known domains.
                if url.startswith("http://"):
                    url = "https://" + url[7:]
                if not allowed_url(url, source["domains"]):
                    continue
                key = article_id(url)
            except ValueError:
                continue
            if key in seen:
                continue
            seen.add(key)
            summary = clean_text(entry.get("summary", ""))[:6000]
            articles.append(
                {
                    "id": key,
                    "title": clean_text(entry.get("title", "Untitled")),
                    "url": url,
                    "source_id": source_id,
                    "source": source["name"],
                    "published_at": published.isoformat()
                    if source_id != "arxiv" and entry.get("published_parsed")
                    else None,
                    "first_published_at": published.isoformat()
                    if source_id != "arxiv" and entry.get("published_parsed")
                    else None,
                    "announced_at": published.isoformat() if source_id == "arxiv" else None,
                    "updated_at": published.isoformat()
                    if source_id != "arxiv" and not entry.get("published_parsed")
                    else None,
                    "feed_entry_at": published.isoformat(),
                    "time_basis": "feed_announcement"
                    if source_id == "arxiv"
                    else ("source_published" if entry.get("published_parsed") else "source_updated"),
                    "text": summary,
                    "evidence_kind": "feed_summary",
                    "demo": False,
                }
            )
        return sorted(articles, key=lambda item: item["feed_entry_at"], reverse=True)[:40]

    def read(self, article):
        source = SOURCES[article["source_id"]]
        payload = self.download(article["url"], source["domains"])
        soup = BeautifulSoup(payload, "html.parser")
        article.update(extract_dates(soup, article["source_id"]))
        main = soup.find("article") or soup.find("main") or soup
        if article["source_id"] == "arxiv":
            # arXiv /abs/ is an abstract page, not the PDF. Avoid returning page navigation.
            abstract = soup.select_one("blockquote.abstract")
            if abstract:
                main = abstract
                article["evidence_kind"] = "abstract"
        text = clean_text(str(main))[:12000]
        if len(text) < 100:
            raise ValueError("正文过短，无法可靠提取")
        return text


def demo_articles(now=None):
    """Intentionally fictional, labelled fixtures; never used by live mode."""
    now = now or datetime.now(UTC)
    rows = [
        (
            "agent",
            "示例：Agent 工具执行器新增失败重试",
            "AI 编程 Agent 工具调用 代码生成",
            "这是虚构演示数据。示例项目把工具失败记录为结构化结果，允许模型重新选择工具。开发者仍需设置最大重试次数和执行超时，避免任务无限循环。",
        ),
        (
            "local",
            "示例：开源模型发布本地部署指南",
            "开源模型 本地部署 local inference",
            "这是虚构演示数据。示例指南展示量化模型在本地运行的过程，列出显存占用、上下文长度与响应速度的取舍，并提供可重复执行的评测脚本。",
        ),
        (
            "research",
            "示例：研究讨论长上下文评测方法",
            "研究论文 论文 研究 大模型 机器学习",
            "这是虚构演示数据。示例论文通过控制输入长度与检索难度比较模型表现。结论只适用于文中测试集，不能直接推断所有生产任务的表现。",
        ),
        (
            "product",
            "示例：AI 工作台加入文档问答",
            "AI 产品 应用 知识库 RAG",
            "这是虚构演示数据。示例产品允许用户上传文档并获得附带原文片段的回答。上线前仍需验证权限隔离、引用准确率与文档更新后的检索效果。",
        ),
        (
            "funding",
            "示例：AI 公司公布融资计划",
            "融资 行业 商业",
            "这是虚构演示数据。示例公司公布融资计划，用于演示排除关键词的效果。用户排除融资时，这条内容不应出现在简报中。",
        ),
    ]
    return [
        {
            "id": f"demo-{slug}",
            "title": title,
            "url": "",
            "source_id": "demo",
            "source": "本地演示样例",
            "published_at": (now - timedelta(hours=i + 1)).isoformat(),
            "text": body,
            "tags": tags,
            "evidence_kind": "demo_fixture",
            "demo": True,
        }
        for i, (slug, title, tags, body) in enumerate(rows)
    ]
