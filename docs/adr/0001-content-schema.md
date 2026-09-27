---
status: accepted
---

# Scraped content lives in its own Postgres schema

The event scraper is being folded into this repo as the `scrape-events` job, at
which point one application writes both user data (`accounts`, `meetups`,
`meetup_participants`) and bulk-ingested content (listings, occurrences, events
and the tables behind deduplication). Those two sets have nothing in common:
user data is authored, small and irreplaceable; content is machine-ingested,
much larger, and fully regenerable by re-scraping in 15–25 minutes. We put the
content tables in a `content` schema and leave the application's own tables in
`public`, so the scraping job can connect as a role with no write grant on
`public`.

## Considered Options

**Two separate databases** — the question that started this. Rejected because a
foreign key cannot cross a database, and a `meetup_events` link table (pinning a
found event to a meetup) is a plausible near-term feature. Across two databases
that becomes a bare integer with no referential integrity, and a re-scrape that
deletes an event leaves it dangling. Separate databases also cost two engines,
two session dependencies, two Alembic histories, two backup procedures and
PostGIS installed twice — permanent overhead for a boundary a schema already
draws. Revisit only on a measured scale trigger: scraper write load or vacuum
bloat hurting user-facing latency, or wanting a read replica for content alone.

**One shared namespace** — the status quo. Rejected because it offers no way to
stop the scraper touching user tables, and the scraper has demonstrated the
need: `mark_disappeared` deletes with no volume floor and there is no
concurrency guard, so a broken source or two overlapping runs can mass-delete.
`SCRAPER_TABLES` in `migrations/env.py` — a hand-maintained list of eleven table
names — exists only because two writers share one namespace, and it is the one
thing standing between `alembic --autogenerate` and 54 destructive operations.

## Consequences

Cross-schema foreign keys work normally inside one database, so nothing about
the modular boundaries changes: a link table from `public` to `content` is
still possible.

`SCRAPER_TABLES` and the `include_object` branch reading it both disappear —
Alembic filters by schema instead of by a hand-maintained blocklist.

`migrations/env.py` pins `search_path=public` to keep PostGIS's tiger tables out
of autogenerate; that becomes `public,content` and needs `include_schemas`.

Because no scraped data has to survive, this lands as an ordinary migration
against freshly created tables rather than a baseline-and-stamp against the live
schema. Only the 54 `venue_links` rows — human verdicts on ambiguous venue
names, which cannot be regenerated — are exported first.
