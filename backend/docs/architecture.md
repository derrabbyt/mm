# Architecture

A modular monolith: one repo, one package, one image, several containers.

## Layout

```
app/
├── api/main.py        the FastAPI shell — which modules are exposed over HTTP
├── jobs/              the scheduled-job runtime — runner, registry
├── modules/           the features, one folder each
├── core/              config, logging, http, redis, exceptions, enums, contracts
├── db/                Base and session — imported by every module, imports none
├── metadata.py        every model, for Alembic — imports every module, imported by none
└── data/              reference data and baked datasets
```

A module owns everything specific to one business capability:

```
modules/meetups/
├── router.py      the door the frontend comes through
├── public.py      the door other backend modules come through
├── service.py     business logic
├── repository.py  SQLAlchemy
├── schemas.py     HTTP request/response bodies
├── models.py      tables
├── jobs.py        scheduled entry points (only where there are any)
└── <name>.py      pure domain logic, named for what it does
```

That last one is a category, not a file: `normalize.py`, `dedup.py`,
`categories.py`, `region.py`. Values in, values out, no session and no import
outside `core` - so it is neither a service (it orchestrates nothing) nor a
repository (it touches no storage). Put it at the top of the module that needs
it.

"Touches no storage" means the database, which is what `repository.py` exists to
own. `events/archive.py` is the exception that shows where the line actually
falls: it writes fetched payloads to a directory, so it is not pure, but nothing
it writes is state the application reads back - it is evidence for a person
debugging a parser, thrown away on a schedule and reproducible by re-scraping.
A file like that stays out of `repository.py`, because putting it there would
hand a session-holding layer a job that needs no session. Logic that turns out to be shared gets a named file in `core/` instead, the
way `contracts.py` and `enums.py` did; there is no `utils.py`, and adding one
would recreate the dumping ground `core/` exists to avoid.

A module may also own a folder of adapters, where one capability means talking
to many outside things that differ only in their details. `events/sources/` is
the one: twenty-one sites, each with its own fetch and parse, behind one
contract in `sources/spec.py`. They are discovered rather than listed, so adding
a Source is adding a file. Nothing in there holds a session, and the boundary
rules apply to it exactly as they do to the module's top level.

What only the adapters use sits beside them rather than at the module's top
level: `sources/dates.py` reads both the ISO timestamps the JSON Sources get
subtly wrong and a German listing page's year-less dates, `sources/jsonld.py`
walks the schema.org data three of them embed, and `sources/markup.py` finds the
one picture that represents a happening. The last is named for what it reads
because `html` is a stdlib module. A helper lands here when the Source that needs
it does, not before - and moves to `core/` if a second module ever needs it,
which is where the HTTP client went once the geocoder wanted one too.

A Listing with no position cannot be found near a Rendezvous at all, because the
read filters on distance, and most Sources publish no coordinates. Turning an
address into a position is `geocoding`'s job, and it is reached as a capability
rather than an import — see "A job that needs two modules" below.

The layering inside a module is described in `auth-and-db-patterns.md`.

## The two entry points

Both paths run the same code. That is the whole point of the arrangement:

```
router.py  →  service.py  →  repository.py
jobs.py    →  service.py  →  repository.py
```

A job that reimplements what an endpoint already does is a bug, not a
shortcut — the two will drift.

## Module boundaries

A module has exactly two doors:

```
frontend  ──HTTP──►  router.py  ─┐
                                 ├─►  service.py  ─►  repository.py
other modules  ──►  public.py  ──┘
```

`public.py` is to other backend modules what `router.py` is to the frontend:
the whole surface, and the only thing anyone outside is allowed to touch.
Everything else in the folder is internal. Importing `meetups.service` or
`meetups.models` from another module is a violation even though it would work,
and `tests/test_module_boundaries.py` fails the build on it.

## Modules do not couple through the database

The second rule, and the one that gives the first its teeth:

> The layers that hold a `Session` - `service.py`, `repository.py`,
> `models.py`, `jobs.py` - may not import another module at all.

Composition happens in `router.py`, which owns the request's session and is
free to call several modules and pass values between them. So a service can
never reach another module's tables, not because it has been told not to but
because it has not been told the other module exists.

`rendezvous/service.py` is what this buys. It used to take
`(db, meetup_id, account_id)` and fetch what it needed through
`meetups.public`; it never queried meetups' tables, but its signature could not
say so. Now it takes the people and the time and answers with a place:

```python
def compute_rendezvous(starts_at, positioned, excluded_ids) -> RendezvousRead:
```

No session, no imports, and the whole travel-time calculation is testable with
three made-up participants - which is how `tests/test_rendezvous.py` exercises
the tie-break, the wrap-around-midnight matrix choice and the unreachable path
that had no coverage at all before.

The loading moved up into `rendezvous/router.py`:

```python
meetup = meetups.get_owned_meetup(db, meetup_id, account.id)
positioned, excluded = meetups.get_positioned_participants(db, meetup.id)
return service.compute_rendezvous(meetup.starts_at, positioned, excluded)
```

`events/router.py` does the same three lines before calling its own service.
That duplication is deliberate: it is wiring, it is visible, and the
alternative is a module that holds a database on another module's behalf.

Why bother, when the direct import works today: what you reach past is what
you get pinned to. `events` imported `rendezvous.repository.to_local` for
exactly one reason - it needed the dataset's timezone - and that quietly made
an unrelated refactor of the dataset loader into a change to `events`. Going
through `public.py` means each module can rearrange its own insides freely, and
what it owes everyone else is one short, readable file.

Current dependencies, all one-way:

```
              ┌── meetups.public ──────┐
              │                        │
router.py ────┼── rendezvous.public ───┼──► its own service.py
              │                        │
              └── accounts.public ─────┘
```

Which modules each router composes:

| router | calls |
|---|---|
| `meetups` | `accounts` |
| `rendezvous` | `accounts`, `meetups` |
| `events` | `accounts`, `meetups`, `rendezvous` |
| `accounts`, `demo` | nothing |

A module gets a `public.py` when something actually crosses into it - which
includes the job runtime above them, and is why `events` and `geocoding` have one
without either appearing in the table. `demo`, `geodata`, `poi` and `matrix` have
no consumers at all and so have none.
Every arrow above now leaves from a `router.py` - no service imports anything
outside its own module.

Keep exports thin, and prefer exporting a function or a schema over a model.
`meetups.public` exports `MeetupParticipant` - an ORM row, session-bound,
carrying its table with it - because `rendezvous` needs positions and travel
modes per participant and no schema describes that shape yet. That is the
heaviest export in the codebase and the first one to replace if a second
consumer appears.

There are two independent pipelines behind the scheduled jobs, and they share
nothing but the database:

```
geodata ──┬──► matrix ──► rendezvous          OSM extract + GTFS feed,
          └──► poi                            put in shared storage

scrape-events ──► content.listings ──► content.events
                  one row per Source     one per happening,
                  from twenty-one sites  whoever listed it
```

`matrix` and `poi` both read what `geodata` has put in shared storage rather
than calling it - they are sequenced by their schedules, not by an import,
which is why none of the three has a `public.py`. The bake turns the OSM
extract and the GTFS feed into cell-to-cell travel times, and the built
manifest records the exact files it used. Scraping event listings has no input,
schedule or failure mode in common with any of that.

`demo` is a module like any other - the SSE, cache and RQ playground - so that
the top level stays free of one-file `routers/`, `schemas/` and `services/`
folders. It depends on nothing and nothing depends on it.

One thing sits outside a module on purpose: **`core/exceptions.py`**, one
catalogue, because it is also what types the frontend's generated error models.
Splitting it per module would fragment the OpenAPI schema to satisfy a
principle.

## Where a shape lives

There are three kinds of shape, and they answer to different people.

| | lives in | answers to | appears in OpenAPI |
|---|---|---|---|
| request/response | `modules/<x>/schemas.py` | the frontend | yes |
| cross-module | `core/contracts.py` | other backend modules | no |
| module-internal | `modules/<x>/<name>.py` | only that module | no |

An HTTP schema is driven by what one screen needs and by the generated Angular
client; it belongs to the module that serves the endpoint, and moving it away
would put a feature back across two folders for no gain. A contract is driven
by what a sibling module needs; it belongs in `core` so that neither module
owns the vocabulary and no module has to import another's `schemas.py` to
speak it.

The third is a module's own vocabulary, and it earns a file only when several
layers inside the module have to speak it. `events/scraped.py` is the one:
`RawListing` is what a Source's parse yields, `NormalizedListing` is what the
repository writes, `BuiltEvent` is what a day's grouping produces, and
`sources/`, `normalize.py`, `service.py` and `repository.py` all need to name
them. It sits next to the pure-logic files
rather than in `schemas.py` (nothing here is on the wire) or `core/contracts.py`
(no other module may see it). If a second module ever needs one of these, that
is the signal to promote it to `core/contracts.py`, not to import across.

`<X>Ref` is a minimal pointer - an id plus enough to name the thing. `<X>Info`
is a read-only view carrying the fields a consumer actually needs. Both are
values, which is the point: a module handing out an ORM model hands out a
session-bound row, its table and its whole future schema, while a module
handing out an `Info` hands out exactly what it promised. `meetups.public`
answers in `MeetupInfo` and `ParticipantInfo` for that reason - it used to
return `Meetup` and `MeetupParticipant`, and that made every column of those
tables part of its public promise by accident.

Models stay in their module either way. Where a model lives answers "who is
allowed to write this?" - which is why the geocoding cache is declared in
`geocoding` rather than in `events`, the module that will fill it. Alembic is
not a reason to centralise them
(`app/metadata.py` handles that) and neither are cross-module foreign keys,
which SQLAlchemy resolves by table name rather than by import.

An enum used by both a column and the shapes built from it has to sit at or
below `core`, or `core` ends up importing from `modules`. That is
`core/enums.py`, and `TravelMode` is its only resident.

## Nothing depends on anything that depends on it

Both graphs are acyclic - file by file, and layer by layer - and
`tests/test_import_graph.py` keeps them that way.

The layered check is the one that earns its keep. `db/` and `core/` sit below
every module, so a file in either that reaches up into `modules/` makes the
plumbing depend on the features built on it. That is exactly what the model
manifest used to do: `db/registry.py` imported every module's models so Alembic
could see them, while every model imported `db/base.py`. No import cycle - the
two are different files - but at the layer level `db` and the feature modules
each depended on the other.

It now lives at `app/metadata.py`, above the modules rather than beneath them.
Nothing imports it except `migrations/env.py`, so it is free to know about
everything.

## A job that needs two modules

A request that needs two modules composes them in `router.py`: it owns the
session and passes values between them, so neither module has to know the other
exists. A job has no router, and `jobs.py` is one of the layers forbidden from
knowing — it holds a session. So a job that needs two modules composes *above*
them, in `app/jobs/`:

```python
# app/jobs/scrape_events.py
def scrape_events() -> None:
    with SessionLocal() as db:
        stats = events.scrape(db, locate=geocoding.locator(db))
```

`scrape-events` is the first of these. `events` owns the catalogue; `geocoding`
owns the geocoder and its cache; the capability crosses as a `Locate`, the port
declared in `core/contracts.py`. The events module ends up unable to reach
geocoding even by accident, which is stronger than going through its `public.py`
and is what the boundary rule was for.

This is the same move as `app/metadata.py`, which sits above the modules so that
it is allowed to know about all of them. A job owned by one module still lives in
that module's `jobs.py`; only a job that has to compose comes up here.

## The URL tree lives in one place

A rendezvous and the events near it are sub-resources of a meetup, so that is
what the paths say - but they are computed by their own modules, and those
modules must not depend on `meetups` to know where they hang. `app/api/main.py`
assembles the nesting instead:

```python
MEETUP = "/api/meetups/{meetup_id}"

tree = APIRouter()
tree.include_router(meetups_router)
tree.include_router(rendezvous_router, prefix=MEETUP, tags=["meetups"])
tree.include_router(events_router, prefix=MEETUP, tags=["meetups"])
```

`rendezvous/router.py` states only `@router.get("/rendezvous")` and knows
nothing about meetups' URLs. Putting the routes in `meetups` instead would have
made `meetups` depend on `rendezvous` and `events`, which already depend on it -
a cycle. The resource hierarchy and the module graph are different graphs, and
this is where they are reconciled.

One thing to watch: a docstring on a route function is published as that
endpoint's OpenAPI `description` and ends up in the generated Angular client.
Implementation notes on a router therefore go in `#` comments.

## Running the jobs

```bash
python -m app.jobs.runner --list
python -m app.jobs.runner scrape-events
python -m app.jobs.runner scrape-events --every 3600   # development only
```

One process, one job, an exit code that says whether it worked. Production
replaces `--every` with a real scheduler (a Kubernetes CronJob, an ECS
scheduled task, a crontab line) running the same command — the application
code does not change.

A scheduler that fires while the previous run is still going is an ordinary
thing to happen — `scrape-events` takes fifteen to twenty-five minutes on an
hourly schedule — so a job that cannot survive it is not schedulable. The scrape
takes a Postgres advisory lock for the length of a run and the second run exits
0 having done nothing. That belongs to the scrape rather than to the runner: what
is unsafe about two runs is specific to what they write, and `--list` or a
read-only job has nothing to protect.

Add a job in two steps: write the function in the owning module's `jobs.py`,
then name it in `app/jobs/registry.py`. A job that needs *two* modules is written
in `app/jobs/` instead, for the reason given above. The registry stores import
*strings*, not functions, so the runner only imports the module it was asked for
— a scraper container never loads the 1.4 GB travel-time dataset.

Jobs have no request to hang a session off, so they open their own:

```python
from ...db.session import SessionLocal


def scrape_events() -> None:
    with SessionLocal() as db:
        service.scrape(db)
```

Scheduled jobs are their own processes rather than RQ tasks - they need no
producer and no queue, and they are long and heavy enough to want their own
scaling.

RQ is the separate mechanism for request-triggered background work, and `demo`
is currently its only user: `demo/router.py` enqueues, `demo/worker.py`
consumes, and nothing else in the codebase imports `rq`. That is why the worker
sits inside `demo` rather than in `app/jobs/` - a module owning a process entry
point is not where it belongs long-term, but it is the truth today, and the day
something real enqueues it moves up.

## Who connects as what

The API and the RQ worker connect as the application's role. The `scrape-events`
job does not: it connects as `mm_scraper`, which can write the `content` schema
and has no grant at all on `public` - not even `SELECT`. A scrape has no reason
to know who the users are, and a bug in it must not be able to reach them. The
role and its grants come from a migration, and `alter default privileges` covers
content tables added later, so nobody has to remember to grant them.

That is the payoff ADR 0001 chose a separate schema for. It only holds while the
geocode cache stays in `content` alongside the catalogue: put a table the job
writes into `public` and the job needs a grant there, which is the whole
arrangement undone.

Where `SCRAPER_POSTGRES_PASSWORD` is unset the job falls back to the
application's connection and logs a warning on every run, because a deployment
should not be able to think it is isolated when it is not.

## What is still outside, and will not be

Two producers live in their own repos today. Both are being folded in, and the
repo is meant to end up self-contained. The module they land in already exists,
which is the point of having stubbed them:

| today | becomes | when it lands |
|---|---|---|
| **activity-loader** — the payload archive and the scraper's own database role | the `scrape-events` job in `modules/events` | the tables, the scrape, the deduplication and **all twenty-one Sources** are here, with real models and real migrations: a run fills `content.listings` and then groups them into `content.events`, which is what the API serves. A run takes an advisory lock and refuses to retire an implausible share of any one Source, so it is safe to schedule. What is left is the archive and database-role work that lets the standalone scraper be switched off |
| **the ttm repo** — runs the r5py bake, produces the dataset folders under `app/data/` | the `bake-matrices` job in `modules/matrix` | the dataset format stops being a contract with an outside system and becomes one between `matrix` (writer) and `rendezvous` (reader), both in this repo |

One thing in the codebase reads as permanent today and is not:

- `app/data/ttm_backend_integration.md` reads as an integration guide with an
  external producer. It becomes an internal format note.

One packaging consequence: `r5py` is currently a default dependency that nothing
imports, and the obvious move is to drop it. After absorption the `matrix` job
genuinely needs it, and a JDK with it - so the move is a matrix-specific
dependency group, not a deletion, and only that image carries the weight.

## Deployment

One image (`Dockerfile`), one command per role:

```
                          mm-backend
                               │
        ┌──────────────────────┴──────────────────────┐
        ▼                                             ▼
  long-running                              background jobs
  ────────────                              ───────────────
  api      fastapi run app/api/main.py      scrape-events    hourly
  worker   -m app.modules.demo.worker       ingest-geodata   daily
                                            build-pois       daily
                                            bake-matrices    weekly

                                            python -m app.jobs.runner <name>
                                            one run, then exit
```

All six are siblings. Nothing here starts, calls or queues work for anything
else: the four jobs wake on their own schedule, and neither the API nor the rq
worker is involved in running them.

`docker-compose.yml` carries all of them behind profiles, so the default
`docker compose up` still brings up infrastructure only:

```bash
docker compose up                    # postgres, redis, photon
docker compose --profile app up      # + api and worker
docker compose --profile jobs up     # + the scheduled jobs
```

The baked matrices are a mounted volume, not an image layer: 1.4 GB, read only
by the API, written on its own schedule by the matrix job.
