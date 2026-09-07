"""own the scraped content tables

The catalogue the event scraper used to own moves into this repo, into a
`content` schema of its own. Nothing is migrated with it: re-scraping rebuilds
the corpus in 15-25 minutes, so the tables are created empty and the ones the
other project made are dropped. See docs/adr/0001-content-schema.md.

Revision ID: b8e9bf5867be
Revises: bc71d3e55958
Create Date: 2026-09-07 20:18:55.699438

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b8e9bf5867be"
down_revision: str | Sequence[str] | None = "bc71d3e55958"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Everything the activity-loader scraper created in `public`, dropped wholesale.
# Five of them come back under their proper names in `content`; the other five go
# for good - the venue review workflow, the alerting state and the scraper's own
# schema-version marker are all out of scope, and `cards` is superseded by the
# Events table that the deduplication work will add.
#
# CASCADE because several of them carry foreign keys to `events`, and IF EXISTS
# because a fresh database has never had any of them.
FOREIGN_TABLES = (
    "cards",
    "dedup_members",
    "venue_links",
    "occurrences",
    "events",
    "geocode_cache",
    "quarantine",
    "source_runs",
    "alert_state",
    "eventloader_meta",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DROP TABLE IF EXISTS " + ", ".join(FOREIGN_TABLES) + " CASCADE")
    op.execute("CREATE SCHEMA IF NOT EXISTS content")

    op.create_table(
        "geocode_cache",
        sa.Column("query_norm", sa.Text(), nullable=False),
        sa.Column("query_raw", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("precision", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("geocoded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "miss_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.PrimaryKeyConstraint("query_norm", name=op.f("pk_geocode_cache")),
        schema="content",
    )
    op.create_table(
        "listings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column(
            "lang_primary", sa.Text(), server_default=sa.text("'de'"), nullable=False
        ),
        sa.Column("title_de", sa.Text(), nullable=True),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("description_de", sa.Text(), nullable=True),
        sa.Column("description_en", sa.Text(), nullable=True),
        sa.Column(
            "title_norm", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("venue_name_norm", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("origin_url", sa.Text(), nullable=True),
        sa.Column("ticket_url", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("venue_name_raw", sa.Text(), nullable=True),
        sa.Column("street", sa.Text(), nullable=True),
        sa.Column("postcode", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column(
            "geo_source", sa.Text(), server_default=sa.text("'none'"), nullable=False
        ),
        sa.Column("geo_precision", sa.Text(), nullable=True),
        sa.Column(
            "categories_raw",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "category", sa.Text(), server_default=sa.text("'other'"), nullable=False
        ),
        sa.Column("price_min", sa.Float(), nullable=True),
        sa.Column("price_max", sa.Float(), nullable=True),
        sa.Column("price_currency", sa.Text(), nullable=True),
        sa.Column("is_free", sa.Boolean(), nullable=True),
        sa.Column("organizer", sa.Text(), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("first_seen_run", sa.Text(), nullable=True),
        sa.Column("last_seen_run", sa.Text(), nullable=True),
        sa.Column("disappeared_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listings")),
        sa.UniqueConstraint(
            "source", "source_event_id", name="uq_listings_source_event"
        ),
        schema="content",
    )
    op.create_index(
        "ix_listings_live",
        "listings",
        ["source"],
        unique=False,
        schema="content",
        postgresql_where=sa.text("disappeared_at IS NULL"),
    )
    op.create_index(
        "ix_listings_origin_url",
        "listings",
        ["origin_url"],
        unique=False,
        schema="content",
    )
    op.create_index(
        "ix_listings_position",
        "listings",
        ["lat", "lon"],
        unique=False,
        schema="content",
    )
    op.create_index(
        "ix_listings_title_norm",
        "listings",
        ["title_norm"],
        unique=False,
        schema="content",
    )
    op.create_index(
        "ix_listings_venue_name_norm",
        "listings",
        ["venue_name_norm"],
        unique=False,
        schema="content",
    )
    op.create_table(
        "quarantine",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quarantine")),
        schema="content",
    )
    op.create_index(
        "ix_quarantine_run_source",
        "quarantine",
        ["run_id", "source"],
        unique=False,
        schema="content",
    )
    op.create_table(
        "source_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "fetched_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "requests", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "raw_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "http_status_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "parsed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "valid_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "quarantined_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "occurrences_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_runs")),
        sa.UniqueConstraint("run_id", "source", name="uq_source_runs_run_source"),
        schema="content",
    )
    op.create_index(
        "ix_source_runs_source_started",
        "source_runs",
        ["source", sa.literal_column("started_at DESC")],
        unique=False,
        schema="content",
    )
    op.create_table(
        "occurrences",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_local", sa.DateTime(), nullable=False),
        sa.Column("end_local", sa.DateTime(), nullable=True),
        sa.Column("date_local", sa.Date(), nullable=False),
        sa.Column("night_of", sa.Date(), nullable=True),
        sa.Column(
            "all_day", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "duration_days", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_seen_run", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["content.listings.id"],
            name=op.f("fk_occurrences_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_occurrences")),
        sa.UniqueConstraint(
            "listing_id", "start_utc", name="uq_occurrences_listing_start"
        ),
        schema="content",
    )
    op.create_index(
        "ix_occurrences_date_local",
        "occurrences",
        ["date_local"],
        unique=False,
        schema="content",
    )
    op.create_index(
        "ix_occurrences_night_of",
        "occurrences",
        ["night_of"],
        unique=False,
        schema="content",
    )
    op.create_index(
        "ix_occurrences_range",
        "occurrences",
        ["start_utc", "end_utc"],
        unique=False,
        schema="content",
    )


def downgrade() -> None:
    """Downgrade schema.

    The `content` schema goes; the tables this migration dropped do not come
    back. They were another project's, created by its own DDL on every connect
    rather than by a migration, and held nothing that is not regenerable by
    re-scraping - so recreating them empty would restore a shape, not data.
    """
    op.execute("DROP SCHEMA IF EXISTS content CASCADE")
