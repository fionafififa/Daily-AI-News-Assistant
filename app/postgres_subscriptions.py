"""Read and write formal subscription records in PostgreSQL."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update

from .db_schema import users
from .schemas import Subscription


def decode_user(row):
    # Keep the API's existing flat shape; the stored primary key is authoritative.
    return {**row["profile"], "id": row["id"]}


class PostgresSubscriptions:
    """The caller owns the engine and disposes it when the application or script stops."""

    def __init__(self, engine):
        self.engine = engine

    def save_user(self, profile, user_id=None):
        """Create a subscription, or replace the profile of an existing subscription."""
        profile = Subscription.model_validate(profile).model_dump(mode="json")

        # A successful block commits; exceptions roll back before propagating.
        with self.engine.begin() as connection:
            if user_id is None:
                statement = insert(users).values(
                    id=uuid4().hex,
                    profile=profile,
                    created_at=datetime.now(UTC),
                )
            else:
                statement = update(users).where(users.c.id == user_id).values(profile=profile)

            result = connection.execute(statement.returning(users.c.id, users.c.profile))
            row = result.mappings().one_or_none()
            if row is None:
                raise KeyError(user_id)
            saved = decode_user(row)

        # Returning after the with block means the database has accepted the commit.
        return saved

    def user(self, user_id):
        with self.engine.connect() as connection:
            statement = select(users.c.id, users.c.profile).where(users.c.id == user_id)
            row = connection.execute(statement).mappings().one_or_none()
            return decode_user(row) if row is not None else None

    def users(self):
        with self.engine.connect() as connection:
            statement = select(users.c.id, users.c.profile).order_by(users.c.created_at, users.c.id)
            rows = connection.execute(statement).mappings().all()
            return [decode_user(row) for row in rows]
