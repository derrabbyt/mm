"""name a listing's source reference for what it is

`source_event_id` used "event" for the id a Source gives one of its own records,
while the glossary reserves Event for the real-world happening several Listings
are deduplicated into. Renamed before the Events table arrives and makes the
collision permanent.

A rename rather than an edit to the migration that created the column: that one
has been applied, and rewriting applied history is worse than one extra
revision.

Revision ID: d7eb4d06c3d0
Revises: b8e9bf5867be
Create Date: 2026-09-27 12:50:58.956394

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7eb4d06c3d0"
down_revision: str | Sequence[str] | None = "b8e9bf5867be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "listings", "source_event_id", new_column_name="source_ref", schema="content"
    )
    op.alter_column(
        "quarantine", "source_event_id", new_column_name="source_ref", schema="content"
    )
    op.execute(
        "ALTER TABLE content.listings "
        "RENAME CONSTRAINT uq_listings_source_event TO uq_listings_source_ref"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE content.listings "
        "RENAME CONSTRAINT uq_listings_source_ref TO uq_listings_source_event"
    )
    op.alter_column(
        "quarantine", "source_ref", new_column_name="source_event_id", schema="content"
    )
    op.alter_column(
        "listings", "source_ref", new_column_name="source_event_id", schema="content"
    )
