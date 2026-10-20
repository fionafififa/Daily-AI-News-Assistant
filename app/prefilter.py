"""Deterministic candidate gates; semantic relevance remains the model's job."""

import re
from collections import Counter

from .sources import canonical_url, normalized_title


def url_key(item):
    try:
        return canonical_url(item.get("url", ""))
    except ValueError:
        # Source adapters validate URLs. Missing legacy history URLs must not
        # accidentally make unrelated articles duplicates of one another.
        return None


def explicit_title_exclusion(title, terms):
    """Only literal title matches; Latin terms must end at word boundaries.

    A body mentioning a topic is insufficient for this early gate. No positive
    keyword whitelist, translation, fuzzy match or semantic inference is used.
    """
    for value in terms:
        term = value.strip()
        if not term:
            continue
        left = r"(?<![a-z0-9_])" if term[0].isascii() and term[0].isalnum() else ""
        right = r"(?![a-z0-9_])" if term[-1].isascii() and term[-1].isalnum() else ""
        if re.search(left + re.escape(term) + right, title.casefold(), flags=re.IGNORECASE):
            return value
    return None


class HistoryIndex:
    def __init__(self, history):
        self.ids, self.urls, self.titles = {}, {}, {}
        for brief in history:
            for item in brief["items"]:
                self.ids[item["id"]] = item["id"]
                if key := url_key(item):
                    self.urls[key] = item["id"]
                if key := normalized_title(item["title"]):
                    self.titles[key] = item["id"]

    def match(self, item):
        for reason, key, index in (
            ("history_id", item["id"], self.ids),
            ("history_url", url_key(item), self.urls),
            ("history_title", normalized_title(item["title"]), self.titles),
        ):
            if key and key in index:
                return {"reason": reason, "duplicate_of": index[key]}
        return None


class NewsPrefilter:
    def __init__(self, history, excludes, enabled=True):
        self.history = HistoryIndex(history)
        self.excludes = excludes
        self.enabled = enabled

    def apply(self, articles, candidates):
        known_ids = set(candidates)
        known_urls = {url_key(item): key for key, item in candidates.items() if url_key(item)}
        kept, decisions = [], []
        for item in articles:
            key = url_key(item)
            rejection = None
            if self.enabled:
                if item["id"] in known_ids:
                    rejection = {"reason": "duplicate_id", "duplicate_of": item["id"]}
                elif key and key in known_urls:
                    rejection = {"reason": "duplicate_url", "duplicate_of": known_urls[key]}
                else:
                    rejection = self.history.match(item)
                if rejection is None and (term := explicit_title_exclusion(item["title"], self.excludes)):
                    rejection = {"reason": "excluded_title", "matched_term": term}
            decision = {"id": item["id"], "url": item.get("url"), "title": item["title"]}
            if rejection:
                decision.update(action="filtered", **rejection)
            else:
                kept.append(item)
                known_ids.add(item["id"])
                if key:
                    known_urls[key] = item["id"]
                decision.update(action="kept", reason="eligible" if self.enabled else "disabled")
            decisions.append(decision)
        summary = {
            "enabled": self.enabled,
            "raw_count": len(articles),
            "kept_count": len(kept),
            "filtered_count": len(articles) - len(kept),
            "reasons": dict(Counter(d["reason"] for d in decisions if d["action"] == "filtered")),
        }
        return kept, summary, decisions
