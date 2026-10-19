"""Run: python -m scripts.check_sources. Prints metadata only; never calls a model."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from app.sources import SOURCES, NewsClient


def check(source_id):
    try:
        articles = NewsClient().fetch(source_id)
        return {
            "source": source_id,
            "status": "ok",
            "last_24h_count": len(articles),
            "latest": articles[0].get("feed_entry_at", articles[0]["published_at"]) if articles else None,
        }
    except Exception as exc:
        return {"source": source_id, "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=5) as pool:
        print(
            json.dumps(
                {"checked_at": datetime.now(UTC).isoformat(), "sources": list(pool.map(check, SOURCES))},
                ensure_ascii=False,
                indent=2,
            )
        )
