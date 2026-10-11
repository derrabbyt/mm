"""give the scraping job its own role

The scraping job stops connecting as the application. It gets a role that can
write the `content` schema and has no grant at all on `public` - so a bug in
scraping cannot reach an account or a meetup. This is the reason ADR 0001 chose
a separate schema over a shared namespace, and it is not delivered until the
grants exist and the job uses them.

The grants live in a migration rather than in a runbook because they are part of
the schema: a content table added later is covered by `alter default
privileges`, and nobody has to remember to run anything by hand against a live
database.

The role itself is created here too, so a fresh database is complete. Its
password is the one thing that cannot be: a migration is committed and a
credential is not. Where `SCRAPER_POSTGRES_PASSWORD` is configured the role is
given it and can log in; where it is not, the role and its grants still exist
and the job connects as the application, saying so loudly. Rotating the password
afterwards is a deployment action - this migration sets it only when creating
the role, so re-running it never resets one.

Because it reads a credential from the environment, this one revision cannot be
rendered with `alembic upgrade --sql`; the role and its password are provisioned
by running it against the database, and everything else it does is ordinary DDL.

Revision ID: a4f1c7d2e830
Revises: f6aab2d86b78
Create Date: 2026-09-28 14:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import settings
from app.db.base import CONTENT_SCHEMA

revision: str = "a4f1c7d2e830"
down_revision: str | Sequence[str] | None = "f6aab2d86b78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "mm_scraper"

TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def upgrade() -> None:
    bind = op.get_bind()
    quoted = _quoted_role()

    existing = bind.execute(
        sa.text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :role"),
        {"role": ROLE},
    ).scalar()

    if existing is None:
        op.execute(f"CREATE ROLE {quoted} NOLOGIN")

    # Only on a role that could not already log in, so re-running a migration
    # never resets a password somebody rotated.
    if settings.scraper_postgres_password and not existing:
        # Postgres does the quoting, via `format`'s %L, and hands back the
        # statement to run. Interpolating the password into SQL here - even
        # inside a dollar-quoted DO block - would let one containing `$$` end
        # the block early and the rest of it be read as statements.
        statement = bind.execute(
            sa.text("SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :role, :pw)"),
            {"role": ROLE, "pw": settings.scraper_postgres_password},
        ).scalar()
        op.execute(statement)

    op.execute(f"GRANT USAGE ON SCHEMA {CONTENT_SCHEMA} TO {quoted}")
    op.execute(
        f"GRANT {TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA {CONTENT_SCHEMA} TO {quoted}"
    )
    op.execute(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {CONTENT_SCHEMA} TO {quoted}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {CONTENT_SCHEMA} "
        f"GRANT {TABLE_PRIVILEGES} ON TABLES TO {quoted}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {CONTENT_SCHEMA} "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {quoted}"
    )

    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {quoted}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {quoted}")
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {quoted}")
    # Revoking from the role is not enough on its own: before Postgres 15 the
    # `public` schema granted CREATE to `PUBLIC`, which every role holds, so the
    # scraper could create tables in it however little it was granted directly.
    # A no-op on 15 and later, which is what this project runs - here so that the
    # guarantee comes from the migration rather than from the server's version.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def downgrade() -> None:
    quoted = _quoted_role()

    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {CONTENT_SCHEMA} "
        f"REVOKE {TABLE_PRIVILEGES} ON TABLES FROM {quoted}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {CONTENT_SCHEMA} "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {quoted}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {CONTENT_SCHEMA} FROM {quoted}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {CONTENT_SCHEMA} FROM {quoted}")
    op.execute(f"REVOKE ALL ON SCHEMA {CONTENT_SCHEMA} FROM {quoted}")


def _quoted_role() -> str:
    if not ROLE.replace("_", "").isalnum():
        raise ValueError(f"unusable scraper role name: {ROLE!r}")
    return f'"{ROLE}"'
