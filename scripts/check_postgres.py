from sqlalchemy import create_engine, text

engine = create_engine("postgresql+psycopg://ai_news:ai_news_dev@127.0.0.1:5432/ai_news")

try:
    with engine.connect() as connection:
        database = connection.execute(text("SELECT current_database()")).scalar_one()
        print(f"连接成功，数据库：{database}")
finally:
    engine.dispose()
