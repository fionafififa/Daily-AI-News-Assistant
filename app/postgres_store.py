"""Complete PostgreSQL implementation of the application's synchronous Store interface."""

import json
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import inspect, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from .db_schema import metadata, notifications, runs, traces
from .postgres_subscriptions import PostgresSubscriptions
from .storage import dumps


def decode_record(row):
    if row is None:
        return None
    return {
        key: value.isoformat() if isinstance(value, (date, datetime)) else value for key, value in row.items()
    }


class PostgresStore(PostgresSubscriptions):
    backend = "postgresql"

    def ensure_ready(self):
        with self.engine.connect() as connection:
            existing = set(inspect(connection).get_table_names())
            if not (set(metadata.tables) | {"alembic_version"}) <= existing:
                raise ValueError("PostgreSQL 表结构未就绪，请先运行 python -m alembic upgrade head。")
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
            if revision != "0002":
                raise ValueError("PostgreSQL 迁移版本不匹配，请先运行 python -m alembic upgrade head。")

    def close(self):
        self.engine.dispose()

    def new_run(self, user_id, mode, trigger="manual", scheduled_date=None):
        values = {
            "id": uuid4().hex,
            "user_id": user_id,
            "mode": mode,
            "status": "queued",
            "trigger": trigger,
            "scheduled_date": date.fromisoformat(scheduled_date)
            if isinstance(scheduled_date, str)
            else scheduled_date,
            "created_at": datetime.now(UTC),
        }
        try:
            with self.engine.begin() as connection:
                row = connection.execute(insert(runs).values(**values).returning(runs)).mappings().one()
                saved = decode_record(row)
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if exc.orig.sqlstate == "23505" and constraint in {
                "one_active_run",
                "uq_runs_user_scheduled_date",
            }:
                return None
            if exc.orig.sqlstate == "23503" and constraint == "fk_runs_user_id_users":
                raise KeyError(user_id) from None
            raise
        return saved

    def run(self, run_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(runs).where(runs.c.id == run_id)).mappings().one_or_none()
            return decode_record(row)

    def runs(self, user_id, limit=60):
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(runs)
                .where(runs.c.user_id == user_id)
                .order_by(runs.c.created_at.desc(), runs.c.id.desc())
                .limit(limit)
            ).mappings()
            return [decode_record(row) for row in rows]

    def update_run(self, run_id, **values):
        allowed = {"status", "completed_at", "error", "brief", "delivery_status", "delivery_error"}
        if not values or set(values) - allowed:
            raise ValueError("Unsupported update")
        if isinstance(values.get("completed_at"), str):
            values["completed_at"] = datetime.fromisoformat(values["completed_at"])
        if "brief" in values:
            values["brief"] = json.loads(dumps(values["brief"]))
        with self.engine.begin() as connection:
            connection.execute(update(runs).where(runs.c.id == run_id).values(**values))

    def recover(self):
        with self.engine.begin() as connection:
            connection.execute(
                update(runs)
                .where(runs.c.status.in_(["running", "queued"]))
                .values(
                    status="failed",
                    error="服务重启，任务已中断；可手动重新生成。",
                    completed_at=datetime.now(UTC),
                )
            )
            connection.execute(
                update(runs)
                .where(runs.c.status == "completed", runs.c.delivery_status.in_(["sending", "pending"]))
                .values(
                    delivery_status="unknown",
                    delivery_error="服务在推送期间重启，请核对收件箱后决定是否重试。",
                )
            )

    def trace(self, run_id, tool, arguments, result, status, duration_ms):
        encoded_args = dumps(arguments)
        if len(encoded_args) > 24000:
            arguments = {"truncated_preview": encoded_args[:24000]}
        else:
            arguments = json.loads(encoded_args)
        with self.engine.begin() as connection:
            connection.execute(
                insert(traces).values(
                    run_id=run_id,
                    tool=tool,
                    arguments=arguments,
                    result=dumps(result)[:24000],
                    status=status,
                    duration_ms=duration_ms,
                    created_at=datetime.now(UTC),
                )
            )

    def traces(self, run_id):
        with self.engine.connect() as connection:
            rows = connection.execute(select(traces).where(traces.c.run_id == run_id).order_by(traces.c.id))
            return [decode_record(row) for row in rows.mappings()]

    def notify(self, user_id, run_id, title):
        with self.engine.begin() as connection:
            connection.execute(
                insert(notifications)
                .values(user_id=user_id, run_id=run_id, title=title, created_at=datetime.now(UTC))
                .on_conflict_do_nothing(constraint="uq_notifications_run_id")
            )

    def notifications(self, user_id, after=0):
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(notifications)
                .where(notifications.c.user_id == user_id, notifications.c.id > after)
                .order_by(notifications.c.id)
                .limit(100)
            )
            return [decode_record(row) for row in rows.mappings()]

    def claim_delivery(self, run_id):
        with self.engine.begin() as connection:
            claimed = connection.execute(
                update(runs)
                .where(
                    runs.c.id == run_id,
                    runs.c.status == "completed",
                    runs.c.delivery_status.in_(["pending", "failed", "unknown"]),
                )
                .values(delivery_status="sending", delivery_error=None)
                .returning(runs.c.id)
            ).scalar_one_or_none()
        return claimed is not None
