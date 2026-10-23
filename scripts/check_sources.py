"""Run: python -m scripts.check_sources. Prints metadata only; never calls a model."""

import asyncio
import json
from datetime import UTC, datetime

from app.sources import SOURCES, NewsClient


async def check(source_id):
    try:
        articles = await NewsClient().fetch(source_id)
        return {
            "source": source_id,
            "status": "ok",
            "last_24h_count": len(articles),
            "latest": articles[0].get("feed_entry_at", articles[0]["published_at"]) if articles else None,
        }
    except Exception as exc:
        return {"source": source_id, "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


async def main():
    semaphore = asyncio.Semaphore(5)

    async def limited_check(source_id):
        async with semaphore:
            return await check(source_id)

    results = await asyncio.gather(*(limited_check(source_id) for source_id in SOURCES))
    print(
        json.dumps(
            {"checked_at": datetime.now(UTC).isoformat(), "sources": results},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
