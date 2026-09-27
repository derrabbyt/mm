"""build Events from grouped Listings

The deduplicated read surface the API serves. A run groups each day's Listings
and writes one `events` row per group, rendering the richest Listing of the
group and gap-filling it from the rest; `event_listings` records which Listings
that was, and is how the read reaches their Occurrences for the time.

Created empty like the rest of the content schema - an Event is derived, so the
first run after this rebuilds every one of them.

Revision ID: f6aab2d86b78
Revises: d7eb4d06c3d0
Create Date: 2026-09-27 14:21:26.507142

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6aab2d86b78"
down_revision: str | Sequence[str] | None = "d7eb4d06c3d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("date_local", sa.Date(), nullable=False),
        sa.Column(
            "duration_days", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("primary_listing_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "lang_primary", sa.Text(), server_default=sa.text("'de'"), nullable=False
        ),
        sa.Column("title_de", sa.Text(), nullable=True),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("description_de", sa.Text(), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column("venue_name_raw", sa.Text(), nullable=True),
        sa.Column("street", sa.Text(), nullable=True),
        sa.Column("postcode", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("origin_url", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("built_run", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["primary_listing_id"],
            ["content.listings.id"],
            name=op.f("fk_events_primary_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("date_local", "group_key", name="uq_events_day_group"),
        schema="content",
    )
    op.create_index(
        "ix_events_day_range",
        "events",
        ["date_local", "duration_days"],
        unique=False,
        schema="content",
    )
    op.create_index(
        "ix_events_position", "events", ["lat", "lon"], unique=False, schema="content"
    )
    op.create_table(
        "event_listings",
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["content.events.id"],
            name=op.f("fk_event_listings_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["content.listings.id"],
            name=op.f("fk_event_listings_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "event_id", "listing_id", name=op.f("pk_event_listings")
        ),
        schema="content",
    )
    op.create_index(
        "ix_event_listings_listing_id",
        "event_listings",
        ["listing_id"],
        unique=False,
        schema="content",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_event_listings_listing_id", table_name="event_listings", schema="content"
    )
    op.drop_table("event_listings", schema="content")
    op.drop_index("ix_events_position", table_name="events", schema="content")
    op.drop_index("ix_events_day_range", table_name="events", schema="content")
    op.drop_table("events", schema="content")
