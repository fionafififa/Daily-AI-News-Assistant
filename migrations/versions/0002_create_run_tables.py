"""Create generation runs, tool traces and notifications with business constraints.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("brief", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("delivery_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_runs_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
        sa.UniqueConstraint("user_id", "scheduled_date", name="uq_runs_user_scheduled_date"),
        sa.UniqueConstraint("id", "user_id", name="uq_runs_id_user_id"),
    )
    op.create_index(
        "one_active_run",
        "runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_runs_user_created_at", "runs", ["user_id", "created_at"])

    op.create_table(
        "traces",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_traces_run_id_runs"),
        sa.PrimaryKeyConstraint("id", name="pk_traces"),
    )
    op.create_index("ix_traces_run_id", "traces", ["run_id", "id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "user_id"],
            ["runs.id", "runs.user_id"],
            name="fk_notifications_run_owner",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.UniqueConstraint("run_id", name="uq_notifications_run_id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id", "id"])


def downgrade():
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_traces_run_id", table_name="traces")
    op.drop_table("traces")
    op.drop_index("ix_runs_user_created_at", table_name="runs")
    op.drop_index("one_active_run", table_name="runs")
    op.drop_table("runs")
