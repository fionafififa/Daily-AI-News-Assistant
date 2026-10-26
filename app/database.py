"""Shared PostgreSQL connection configuration for T02."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from .config import ROOT


def get_database_url(raw_url=None):
    if raw_url is None:
        load_dotenv(ROOT / ".env")
        raw_url = os.getenv("DATABASE_URL")
    if not raw_url:
        raise ValueError(
            "请设置 DATABASE_URL；本地连接示例见 .env.example 和 docs/t02-postgres-migrations.md。"
        )
    database_url = make_url(raw_url)
    if database_url.drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL 请使用 postgresql+psycopg:// 开头的 PostgreSQL 连接地址。")
    return database_url


def create_postgres_engine(database_url=None):
    return create_engine(
        get_database_url(database_url),
        pool_pre_ping=True,
        pool_timeout=10,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=10000 -c lock_timeout=5000",
        },
    )
