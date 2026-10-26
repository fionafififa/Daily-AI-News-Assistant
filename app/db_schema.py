"""PostgreSQL table definitions introduced incrementally during T02."""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})

# users keeps the existing subscription profile and its stable identifier.
users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("profile", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id", name="fk_runs_user_id_users"), nullable=False),
    Column("mode", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("scheduled_date", Date),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("error", Text),
    Column("brief", JSONB(none_as_null=True)),
    Column("delivery_status", Text, nullable=False, server_default=text("'pending'")),
    Column("delivery_error", Text),
    UniqueConstraint("user_id", "scheduled_date", name="uq_runs_user_scheduled_date"),
    # The notification's compound foreign key uses this explicit ownership key.
    UniqueConstraint("id", "user_id", name="uq_runs_id_user_id"),
)
Index(
    "one_active_run",
    runs.c.user_id,
    unique=True,
    postgresql_where=text("status IN ('queued', 'running')"),
)
Index("ix_runs_user_created_at", runs.c.user_id, runs.c.created_at)

traces = Table(
    "traces",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("run_id", Text, ForeignKey("runs.id", name="fk_traces_run_id_runs"), nullable=False),
    Column("tool", Text, nullable=False),
    Column("arguments", JSONB, nullable=False),
    # Existing trace results can contain truncated JSON, so retain their text form.
    Column("result", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
Index("ix_traces_run_id", traces.c.run_id, traces.c.id)

notifications = Table(
    "notifications",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("run_id", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["run_id", "user_id"],
        ["runs.id", "runs.user_id"],
        name="fk_notifications_run_owner",
    ),
    UniqueConstraint("run_id", name="uq_notifications_run_id"),
)
Index("ix_notifications_user_id", notifications.c.user_id, notifications.c.id)
