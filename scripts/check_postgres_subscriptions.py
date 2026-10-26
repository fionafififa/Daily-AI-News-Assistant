"""Create and read one formal subscription. Apply Alembic migrations before running."""

import json

from app.database import create_postgres_engine
from app.postgres_subscriptions import PostgresSubscriptions
from app.schemas import Subscription


def main():
    engine = create_postgres_engine()
    try:
        subscriptions = PostgresSubscriptions(engine)
        profile = Subscription(name="RAG", topics=["RAG"], enabled=False).model_dump()

        saved = subscriptions.save_user(profile, user_id="1924612ae98447899c0aa1d6db1af810")
        print(f"已保存：{saved['id']}")

        loaded = subscriptions.user(saved["id"])
        if loaded != saved:
            raise RuntimeError("查回的订阅与保存结果不一致")
        print("已重新连接查询，内容一致：")
        print(json.dumps(loaded, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
