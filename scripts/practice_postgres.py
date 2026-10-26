"""T02 学习示例：建表、插入、提交，再重新连接查询。"""

from sqlalchemy import create_engine, text


def main():
    engine = create_engine("postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news")
    try:
        # 正常离开 begin 代码块时提交；发生异常时回滚本次修改。
        with engine.begin() as connection:
            connection.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS t02_subscription_demo (
                        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        name TEXT NOT NULL,
                        topic TEXT NOT NULL
                    )
                """)
            )
            subscription_id = connection.execute(
                text("""
                    INSERT INTO t02_subscription_demo (name, topic)
                    VALUES (:name, :topic)
                    RETURNING id
                """),
                {"name": "研究简报", "topic": "RAG"},
            ).scalar_one()

        # 上面的事务已经提交。重新取得连接，查询刚才保存的记录。
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT id, name, topic FROM t02_subscription_demo WHERE id = :id"),
                {"id": subscription_id},
            )
            subscription = result.mappings().one()
            print(f"已保存并查回：{dict(subscription)}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
