---
status: accepted
---

# A job that needs two modules is composed above them

A scrape needs geocoding. The rule that keeps modules from coupling through the
database is that the layers holding a session — `service.py`, `repository.py`,
`models.py`, `jobs.py` — may not import another module at all, so there was no
legal path from `events` to `geocoding`. A request in this position composes the
two in `router.py`, which owns the session and passes values between them; a job
has no router, and `jobs.py` is itself one of the forbidden layers. So the wiring
moved above the modules, into `app/jobs/scrape_events.py`, and the capability
crosses as a `Locate` port declared in `core/contracts.py`.

## Considered Options

**Let `jobs.py` compose, and drop it from the forbidden list.** The tempting one:
`jobs.py` is a job's composition point exactly as `router.py` is a request's, and
`router.py` also holds a session while composing freely. Rejected because the
rule is not incidental — it is the thing that makes `events` unable to reach
another module's tables even by mistake, and relaxing it for the first job that
finds it inconvenient is how that guarantee stops being one.

**Fold geocoding into the events module.** Rejected before this: the
points-of-interest work will want it and the frontend already reverse-geocodes a
Rendezvous, so it is not events' to own.

**A composition file inside `events/` under a name the boundary test does not
check.** Would pass, because the test matches on filename. Rejected as gaming the
check rather than satisfying the rule behind it.

## Consequences

`events` imports `geocoding` nowhere, which is stronger than reaching it through
a `public.py` — the module cannot couple to it even accidentally.

A module still owns its own jobs. Only a job that has to compose comes up to
`app/jobs/`, so the layout does not change for the four that don't.

The two modules also end up with separate sessions, which is correct rather than
incidental: a scrape commits one Source at a time so its Listings and the record
of how it went land together, and a geocoded address is worth keeping whether or
not the Source that prompted the lookup succeeds afterwards.

`app/jobs/` is now a place that may know about every module, like
`app/metadata.py`. That is a small amount of new surface to keep honest: it is
for wiring, and logic appearing there belongs in a module instead.
