---
status: accepted
---

# A run is guarded by a lock and a per-Source volume floor

ADR 0001 named two defects in the scraper being folded in: "`mark_disappeared`
deletes with no volume floor and there is no concurrency guard, so a broken
source or two overlapping runs can mass-delete." Neither had ever mattered,
because the scraper was run by hand. `scrape-events` is scheduled, so both had to
be closed before it could run unattended.

Both defects have the same shape. What says a Listing is still real is the run
that last saw it, so retiring is the one thing a run does that can remove
something a person would have been shown — and it is driven entirely by what a
Source happened to return this time. A second run stamping its own id makes the
first run's Listings look unseen; a site serving one page of the twenty it had
makes nineteen real happenings look cancelled. So there are two guards, and both
are about retiring:

**One run at a time.** The scrape takes a Postgres advisory lock and a run that
cannot get it returns cleanly, having done nothing.

**A volume floor per Source.** A run that would retire more than
`SCRAPE_MAX_RETIRED_SHARE` (0.6) of one Source's live Listings retires nothing
for that Source and logs at ERROR.

## Considered Options

**A transaction-level lock** (`pg_advisory_xact_lock`), which needs no release.
Rejected: a scrape commits once per Source so that a Source's Listings and the
record of how it went land together, and the first of those commits would drop
the lock — leaving the remaining twenty Sources, and all the retiring they do,
unguarded. Session-level, released in a `finally`, and released by the connection
dying if the process is killed.

**Blocking, or waiting with a timeout.** Rejected: the job takes fifteen to
twenty-five minutes on an hourly schedule, so a run that queued behind its
predecessor would still be waiting when the scheduler fired the next one. There
is nothing a second run could add that the first is not already doing.

**A lock in the runner, around every job.** Rejected: what is unsafe about two
runs is specific to what they write. `--list` and a read-only job have nothing to
protect, and a guard in the runner would say the scrape is safe because it ran
alone rather than because anything about the scrape says so.

**The floor as a share of a trailing median, per the scraper's own
`volume_drop` alert.** Rejected as more than is needed: the median is a thing to
maintain, and the question here is only "is this run about to remove most of a
Source?", which the live count already answers. 0.6 is that alert's 40% threshold
read from the other side — a run keeping under 40% of what a Source had was
already the number somebody was expected to look at.

**One floor for the whole run.** Rejected: twenty-one Sources fail
independently, and one site changing its markup must not stop the other twenty
retiring what really ended. Each Source is judged on its own counts.

## Consequences

A scheduler firing on top of a long run is ordinary rather than a failure, so
`scrape-events` is safe to put on a timer — which is what the whole absorption
was for.

**A Source that genuinely shrank that far keeps its dropped Listings for as
long as they are still dated inside the window** — up to sixty days of
happenings that have really been cancelled, still shown. That is the cost of the
guard and it is accepted; it is also why it logs at ERROR rather than at
WARNING, because a real collapse wants a person looking at the Source and no
threshold can tell that case from a broken parser. The alerting that would page
one is out of scope; the per-Source statistics that make it possible are already
recorded.

It does recover on its own, though, which is why the share is taken over the
window rather than over the Source's whole history. Once the dropped Listings'
dates pass they stop counting against the Source, the share falls back under the
floor, and the next run retires them. Measured against everything a Source ever
had, one breach would leave rows that only `retire_unseen` can clear sitting in
the denominator that stops `retire_unseen` running — and the Source could never
retire anything again, including a routine one-Listing drop.

Retiring now has three conditions rather than two, and they read in one place:
`retire_unseen` runs only after a fetch that succeeded, only when the Source
returned something storable, and only when what it would retire is a plausible
share of it. `repository.py` decides none of that — it counts and it updates, and
the service says whether to.
