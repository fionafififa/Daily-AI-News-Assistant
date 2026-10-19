"""Keep feed freshness separate from the original article's publication date."""

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", value):
            return value.replace("/", "-")  # Date precision stays date precision.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    if parsed.tzinfo is None:
        return None  # Do not invent a timezone for ambiguous wall-clock times.
    return parsed.astimezone(UTC).isoformat()


def extract_dates(soup, source_id):
    fields = {}
    for meta in soup.select("meta[content]"):
        key = (meta.get("name") or meta.get("property") or "").lower()
        value = parse_date(meta.get("content"))
        if value and key in {
            "citation_date",
            "citation_publication_date",
            "citation_online_date",
            "article:published_time",
        }:
            fields.setdefault("first_published_at", value)
        elif value and key == "article:modified_time":
            fields.setdefault("updated_at", value)
    if source_id == "arxiv":
        history = soup.select_one(".submission-history")
        text = history.get_text(" ", strip=True) if history else soup.get_text(" ", strip=True)
        versions = re.findall(
            r"\[v(\d+)\]\s*([A-Za-z]{3},?\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC)", text
        )
        parsed = [(int(version), parse_date(date)) for version, date in versions]
        parsed = [(version, date) for version, date in parsed if date]
        if parsed:
            by_version = sorted(parsed)
            if by_version[0][0] == 1:
                fields["first_published_at"] = by_version[0][1]
            if len(by_version) > 1:
                fields["updated_at"] = by_version[-1][1]
        fields["publication_kind"] = "preprint"
    if fields.get("first_published_at"):
        fields["published_at"] = fields["first_published_at"]
    return fields


def freshness_time(article):
    return article.get("feed_entry_at") or article.get("announced_at") or article.get("published_at")


def date_labels(article):
    """Human-readable labels also work for historical records without new fields."""
    if article.get("source_id") == "arxiv":
        parts = ["RSS 公告：" + (article.get("announced_at") or article.get("feed_entry_at") or "未知")]
        parts.append("首次提交：" + (article.get("first_published_at") or "未知"))
        if article.get("updated_at"):
            parts.append("修订：" + article["updated_at"])
        return " | ".join(parts)
    return "来源发布：" + (article.get("first_published_at") or article.get("published_at") or "未知")
