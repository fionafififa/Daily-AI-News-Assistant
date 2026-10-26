"""SQLite storage, shared serialization helpers, and explicit backend selection."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def utcnow():
    return datetime.now(UTC).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    backend = "sqlite"

    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "news.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, profile TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    mode TEXT NOT NULL, status TEXT NOT NULL, trigger TEXT NOT NULL,
                    scheduled_date TEXT, created_at TEXT NOT NULL, completed_at TEXT,
                    error TEXT, brief TEXT, delivery_status TEXT NOT NULL DEFAULT 'pending',
                    delivery_error TEXT,
                    UNIQUE(user_id, scheduled_date)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(user_id)
                    WHERE status IN ('queued', 'running');
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    tool TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL,
                    status TEXT NOT NULL, duration_ms INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def users(self):
        with self.connect() as db:
            return [
                {"id": row["id"], **json.loads(row["profile"])}
                for row in db.execute("SELECT * FROM users ORDER BY created_at")
            ]

    def user(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return {"id": row["id"], **json.loads(row["profile"])} if row else None

    def save_user(self, profile, user_id=None):
        with self.connect() as db:
            if user_id:
                if not db.execute(
                    "UPDATE users SET profile=? WHERE id=?", (dumps(profile), user_id)
                ).rowcount:
                    raise KeyError(user_id)
            else:
                user_id = uuid4().hex
                db.execute("INSERT INTO users VALUES (?, ?, ?)", (user_id, dumps(profile), utcnow()))
        return self.user(user_id)

    def new_run(self, user_id, mode, trigger="manual", scheduled_date=None):
        run_id = uuid4().hex
        with self.connect() as db:
            try:
                db.execute(
                    """INSERT INTO runs
                    (id,user_id,mode,status,trigger,scheduled_date,created_at)
                    VALUES (?,?,?,'queued',?,?,?)""",
                    (run_id, user_id, mode, trigger, scheduled_date, utcnow()),
                )
            except sqlite3.IntegrityError:
                if not self.user(user_id):
                    raise KeyError(user_id)
                return None
        return self.run(run_id)

    @staticmethod
    def decode_run(row):
        if not row:
            return None
        value = dict(row)
        value["brief"] = json.loads(value["brief"]) if value["brief"] else None
        return value

    def run(self, run_id):
        with self.connect() as db:
            return self.decode_run(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())

    def runs(self, user_id, limit=60):
        with self.connect() as db:
            return [
                self.decode_run(row)
                for row in db.execute(
                    "SELECT * FROM runs WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
                )
            ]

    def update_run(self, run_id, **values):
        allowed = {"status", "completed_at", "error", "brief", "delivery_status", "delivery_error"}
        if not values or set(values) - allowed:
            raise ValueError("Unsupported update")
        if "brief" in values:
            values["brief"] = dumps(values["brief"])
        with self.connect() as db:
            db.execute(
                f"UPDATE runs SET {','.join(k + '=?' for k in values)} WHERE id=?", (*values.values(), run_id)
            )

    def recover(self):
        with self.connect() as db:
            db.execute(
                """UPDATE runs SET status='failed', error='服务重启，任务已中断；可手动重新生成。',
                completed_at=? WHERE status IN ('running','queued')""",
                (utcnow(),),
            )
            # Never automatically resend SMTP after a crash: the server may already have accepted it.
            db.execute("""UPDATE runs SET delivery_status='unknown',
                delivery_error='服务在推送期间重启，请核对收件箱后决定是否重试。'
                WHERE status='completed' AND delivery_status IN ('sending','pending')""")

    def trace(self, run_id, tool, arguments, result, status, duration_ms):
        encoded_args = dumps(arguments)
        if len(encoded_args) > 24000:
            encoded_args = dumps({"truncated_preview": encoded_args[:24000]})
        with self.connect() as db:
            db.execute(
                """INSERT INTO traces
                (run_id,tool,arguments,result,status,duration_ms,created_at) VALUES (?,?,?,?,?,?,?)""",
                (run_id, tool, encoded_args, dumps(result)[:24000], status, duration_ms, utcnow()),
            )

    def traces(self, run_id):
        with self.connect() as db:
            return [
                {**dict(row), "arguments": json.loads(row["arguments"]), "result": row["result"]}
                for row in db.execute("SELECT * FROM traces WHERE run_id=? ORDER BY id", (run_id,))
            ]

    def notify(self, user_id, run_id, title):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO notifications(user_id,run_id,title,created_at) VALUES (?,?,?,?)",
                (user_id, run_id, title, utcnow()),
            )

    def notifications(self, user_id, after=0):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM notifications WHERE user_id=? AND id>? ORDER BY id LIMIT 100",
                    (user_id, after),
                )
            ]

    def claim_delivery(self, run_id):
        with self.connect() as db:
            return bool(
                db.execute(
                    """UPDATE runs SET delivery_status='sending',delivery_error=NULL
                WHERE id=? AND status='completed' AND delivery_status IN ('pending','failed','unknown')""",
                    (run_id,),
                ).rowcount
            )

    def ensure_ready(self):
        """SQLite tables are initialized in the constructor."""

    def close(self):
        """SQLite operations already close their short-lived connections."""


def create_store(settings):
    if settings.database_url:
        from .database import create_postgres_engine
        from .postgres_store import PostgresStore

        return PostgresStore(create_postgres_engine(settings.database_url))
    return Store(settings.data_dir)
