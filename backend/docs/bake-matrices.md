# Running the bake

`bake-matrices` turns an OSM extract and a GTFS feed into the Dataset the
rendezvous calculation reads. One process, three hours, and an exit code.

```bash
uv sync --group matrix                          # the routing engine, once
uv run python -m app.jobs.runner bake-matrices
```

In a container it is the one service built from the `matrix` target:

```bash
./scripts/fetch-r5-jar.sh                       # 64 MB, gitignored, once
docker compose build bake-matrices
docker compose --profile jobs up bake-matrices
```

It takes nothing on the command line. What it routes over is
`MATRIX_OSM_PATH` and `MATRIX_GTFS_PATH`; where it publishes is `DATASET_DIR`,
the same folder the API reads; what it builds is `modules/matrix/profile.py` —
the city, the bounding box, the H3 resolution and the grid version, which belong
in a diff rather than in a shell because they define what a Dataset *is*.

It fetches nothing. The geodata job is meant to put the extract and the feed in
shared storage; until it exists, that directory is filled by hand, which is
exactly what happened before.

**Nothing is published if anything fails.** A Dataset that will not load, or
that fails either check in `checks.py`, leaves the previous `manifest.json`
exactly where it is and the API serving it. See ADR 0006.

## The inputs, and where they actually came from

Both were obtained by hand. There is no fetching code for either, anywhere, and
the geodata job that is meant to own this does not exist yet, so this is the
record:

| | file | size | provenance |
|---|---|---|---|
| OSM | `vienna.osm.pbf` | 100 663 540 B | **BBBike.org**, per the PBF header's `source` field (`https://download.BBBike.org`) — a custom-area extract, downloaded by hand. Not, as was assumed, a clip of the Geofabrik Austria file sitting next to it: that one's header says `osmium/1.14.0` over `geofabrik.de/europe/austria-updates`, and the Vienna file has no osmium writer at all. |
| GTFS | `wl.zip` | 51 033 021 B | Wiener Linien. Validity window `2025-12-14`–`2026-12-12`, timezone `Europe/Vienna`, both read out of the feed by `feed.py`. |

Both files are dated 2026-02-18.

The bounding box the shipped Dataset was baked with existed only in the first
line of a log file in the producing repo — the default in that repo's code was a
different box. It is `profile.py` now.

## The packaging gate

Running the bake in CI is three hours, a JVM and roughly four gigabytes of
Python, so the image is verified by hand instead. What has been checked, and
what has not:

**Verified on the host, 2026-10-08.**

- The class path is pinned by `etc/r5py.yml`: the engine resolves to the named
  jar instead of looking for one.
- The heap is pinned to the value the image ships: against `etc/r5py.yml`'s
  `max-memory: 6G` the engine reported a 6.0 GiB heap, and against `2G` it
  reported 2.0 GiB, where unpinned it sized itself at 11.9 GiB — 80% of this
  host's RAM, and it would read the *host's* RAM inside a container too.
- The engine imports with no name resolution at all (`unshare -rn`), with the
  class path pinned. `socket.gethostbyname` raises; `import r5py` does not.
- The jar lives at `/opt/r5/`, outside the engine's cache directory, so the
  cache's two-week expiry cannot delete the dependency a weekly job needs.
- A real bake runs end to end against the real extract and feed: 29 cells and
  663 cells, thirteen matrices each, Departures derived from the feed (a
  Wednesday in September, a Saturday and Sunday in June, all inside the feed's
  `2025-12-14`–`2026-12-12` window), both checks run, published by rename, and
  the result loads in the reader. The Saturday matrices come out *different*
  from the Sunday ones — 884 s mean against 901 s at 10:00 — which is the defect
  in the shipped Dataset, fixed.
- A re-run reuses every finished matrix and republishes a complete manifest, so
  an interrupted bake resumes and the old "already exists" path that wrote a
  manifest the reader rejected is gone.
- The `matrix` dependency group is the only thing that carries the engine: the
  full test suite passes with `r5py` not installed. `numpy` had to become an
  explicit dependency, because it had only ever arrived underneath `r5py` and
  the reader needs it.

**Not verified: the image does not build on this host.** `docker build` has no
name resolution, so `uv sync` cannot reach PyPI — in the *first* stage, the one
the API and every other container already use, so this is the development host
rather than anything about the new target. On a host with working DNS the gate
is:

```bash
./scripts/fetch-r5-jar.sh
docker build --target matrix -t mm-backend-matrix .
docker run --rm mm-backend-matrix python -c "import r5py; print(r5py.__version__)"
docker run --rm mm-backend-matrix python -c \
    "from r5py.util.memory_footprint import MAX_JVM_MEMORY; print(MAX_JVM_MEMORY/1024**3)"
docker run --rm --network none mm-backend-matrix \
    python -c "import r5py; from r5py.util.classpath import R5_CLASSPATH; print(R5_CLASSPATH)"
docker build --target app -t mm-backend . && \
    docker run --rm mm-backend python -c "import importlib.util; \
        print(importlib.util.find_spec('r5py'))"   # must print None
```

**Measured: peak memory is the heap ceiling, not the grid.** Three runs on the
host, against the real extract and feed:

| cells | JVM heap | wall | peak RSS |
|---|---|---|---|
| 29 | pinned `2G` | 1.5 min | **2.42 GiB** |
| 29 | unpinned (80% of a 15 GiB host) | 1.4 min | **7.94 GiB** |
| 663 | unpinned | 22.3 min | **8.22 GiB** |
| 7466 | unpinned | 3 h 08 m (the shipped bake) | not measured |

Twenty-three times the cells — 525 times the cell *pairs* — moved peak memory by
0.3 GiB. Pinning the heap moved it by 5.5 GiB. The JVM runs in-process under
jpype, so its heap is part of this process's RSS, and it helps itself to
whatever ceiling it is given: that is what `max-memory` in `etc/r5py.yml` is
for, and it is why an unpinned bake in a container sizes itself against the
host and gets killed.

What this does **not** settle is the Python term at the full grid. The engine
builds one frame per origin and concatenates all of them, which at 7466 cells is
55.7 million rows, and the research's estimate of roughly 4 GB of Python on top
of the heap comes from reading that code rather than from measuring it. At 663
cells that term is invisible; it is `O(N²)`, so it is also the hard ceiling on
grid resolution. Size the bake's container at the pinned heap plus about four
gigabytes, and measure it on the next full bake:

```bash
/usr/bin/time -v python -m app.jobs.runner bake-matrices   # Maximum resident set size
```

## The one rebuild still owed

**The shipped Dataset fails the Saturday check, and this work does not fix it
for anyone until it is rebuilt.** Verified once, on 2026-10-08, against
`app/data/output_dev/vienna_v2_h3r9`:

```
COVERAGE: passes          (transit matrices reach 20.6–21.8% of cell pairs)
SATURDAY: REJECTED        transit_sat10 and transit_sun10 are identical
```

Its Saturday Departure is `2026-08-15` — Assumption Day, on which Wiener Linien
runs the Sunday timetable. All three Saturday matrices are byte-identical to
their Sunday counterparts, so every Saturday Rendezvous is currently computed
from Sunday service, against an operator whose own feed defines 34 Saturday-only
and 38 Sunday-only service patterns and runs 15% more trips on a Saturday.

That is not asserted by a test. A test that asserted it would fail the moment
somebody did the right thing, and the check that matters — two identical weekend
matrices are never the truth about a city — is tested on its own, against a
Dataset built to have them.

The fix is in the date handling, so it reaches users through **one operator
action, once**. `scripts/rebuild-matrices.sh` walks it:

```bash
./scripts/rebuild-matrices.sh
```

It places the extract and the feed, shows the current Saturday/Sunday verdict
and the Departures the feed will now produce *before* spending three hours,
runs the bake while reading peak memory off the kernel's own high-water mark,
checks what was published, prompts for the API restart that actually carries
the fix, appends the run to `bake-runs.md`, and offers to reclaim the
superseded matrices. The same script is the procedure for an ordinary rebuild,
since a new annual feed lands about every December and the Departures move with
it. By hand it is:

1. Put the extract and the feed where `MATRIX_OSM_PATH` and `MATRIX_GTFS_PATH`
   point.
2. Run `bake-matrices` against the current `DATASET_DIR`. It reuses nothing —
   the Departures have changed, so every transit matrix has a new build hash —
   and takes about three hours. The API keeps serving the old Dataset
   throughout.
3. Restart the API. The reader opens its Dataset at import, so a published
   Dataset is picked up by a restart and not before.
4. Optionally reclaim the superseded `.npy` files the bake named in its log.
   They are the old matrices, kept because a process that has not reopened is
   still mapping them.

Against the Wiener Linien feed on disk, that bake will route its Saturday over
`2026-06-13` and its Sunday over `2026-06-14` — a weekend the feed itself says
is an ordinary one, with 25 and 29 services respectively rather than the same
29 twice.
