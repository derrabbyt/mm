---
status: accepted
---

# The scraped catalogue has exactly one owner

The standalone scraper stops running. This repo scrapes, stores and serves
Events, and nothing outside a migration here touches the schema — so a change to
a Listing's shape is one change in one place.

Until now two projects wrote these tables. The scraper created and migrated
them with a schema-version marker of its own and held write credentials on the
database the backend served live; the backend read them and framed them as
somebody else's. That arrangement is what ADR 0001 began unpicking and what the
work since finished: all twenty-one Sources, the geocoder, the deduplication
pass and the run guards are here, the tables live in a `content` schema this
repo migrates, and the job connects as a role that cannot reach the
application's tables.

What is left is to say the second deployment is gone and to remove the things
that existed only to bridge two projects.

## Considered Options

**Keep the scraper running and let it write `content`.** Rejected: it is the
two-owner problem restated. Two schema-version markers disagreeing is how the
next migration becomes unsafe, and the new database role only constrains the job
in *this* repo — the other deployment holds its own credentials.

**Keep the export directory as a safety artifact.** The scraper wrote a
`jsonl.gz` snapshot per run, and the argument for keeping it was that a bad
scrape is now visible to users immediately with no artifact to inspect first.
Rejected: a database backup is the right tool for that, and the payload archive
covers the case the export was actually used for — working out why a parser
produced nonsense, against the document that produced it.

Its disuse was checked before dropping it, because the research flagged it as
unverified. Nothing reads `data/export/` — not this repo, not the frontend, and
not the scraper, whose own CLI is the only thing that ever wrote it; its
`DESIGN.md` records the export as superseded once the backend began reading
Postgres directly, and `export.enabled` defaults to false. Written it was: five
run directories from 2026-08-11 sit in the scraper's working copy. Nothing has
ever read one.

## The deduplication pass's memory, measured

The research flagged the pass's peak memory as an unverified estimate, so it was
measured here. Against the live catalogue — 3,186 Listings over 7,124
Occurrences — `rebuild_events` peaks at 2.4 MiB of traced allocation, about
5 MiB of process RSS, and the per-day figure that actually bounds it is roughly
8 KiB per Listing: 2.4 MiB on the busiest day in the window at 285 Listings,
1.3 MiB at 162, 0.7 MiB at 136. Linear in one day's Listings, as predicted, and
a day ten times busier would still cost tens of megabytes.

That is a second measurement rather than a contradiction of the research's own
(`dedup-validation.md` §2.3), which sized the *scraper's* structures per row and
reached the same verdict by a different route. This one measures the pass that
actually runs now, which is the one worth recording.

## Consequences

Five of the scraper's ten tables are not carried across. Three were dropped on
purpose — `venue_links`, the venue verdict table behind a human review workflow;
`alert_state`, the alerting transition state; and `eventloader_meta`, which held
the scraper's own `schema_version`. Per-Source run statistics are kept, which is
what makes alerting possible later; alerting itself is out of scope. The other
two were renamed: `cards` is `content.events` and `dedup_members` is
`content.event_listings`. A test asserts all five stay absent from both `public`
and `content` — `public` because that is where the scraper's unqualified
`CREATE TABLE` statements actually built — and that `content` holds exactly what
the models declare, so a later migration cannot quietly restore a second author.

`scrape-events` runs as a container from this repo's image on a schedule,
alongside the other background jobs — the same image the API runs, with a
different command.

No shipped code runs DDL or imports alembic outside `migrations/`, and a test
enforces both. `tests/` is exempt, because a test may build a table to check
that a grant reaches it. The database enforces its own half independently: the
scraping role is granted no `CREATE`.

The other repo is not deleted by this decision; switching its deployment off is
the operator action this ADR records the readiness for.
