# The Dataset format

A Dataset is a folder of travel-time matrices. `modules/matrix` writes one and
`modules/rendezvous` reads one, and both go through `app/core/dataset.py`, which
is the definition — pydantic models, the sentinel, the per-mode ceilings and the
Departure table. This note is the prose around it; the models are the truth, and
a Dataset either validates against them or is rejected naming the field that
failed.

This replaces an integration guide written when the bake lived in another repo.
The format is no longer a contract with an outside producer: it is one between
two modules here, and neither may import the other, which is why it sits in
`core`.

## The folder

```
<city>_<grid_version>/
    manifest.json              what to load, and the exact inputs it was built from
    grid.json                  the cells, frozen for the folder's lifetime
    walk__<build_hash>.npy     (n_cells, n_cells) uint16 seconds
    bike__<build_hash>.npy
    car__<build_hash>.npy
    transit_<bucket>__<build_hash>.npy
```

Two rules for anything that reads one:

**Never scan the directory.** `manifest.matrices` is the index. Filenames carry
a build hash, and a folder legitimately holds matrices from earlier bakes that
nothing should load — the bake leaves them in place because a reader that has
not reopened yet is still mapping them.

**Treat `grid.json` as frozen.** Cell ids are what a matrix's rows and columns
mean. A different grid is a different Dataset and wants a different folder,
which is why the folder name carries the grid version and why the bake verifies
the file rather than rewriting it.

## manifest.json

`core.dataset.Manifest`. Validation is strict — an unknown key is a writer and a
reader disagreeing, so it fails rather than being ignored.

It carries, besides the index: the city, when it was built, the grid's digest,
and `sources`, which records the OSM extract and the transit feed by path, size
and full SHA-256. That is how "which inputs produced this travel time" is
answerable afterwards, and the digests are folded into every build hash.

Per matrix (`core.dataset.MatrixEntry`):

| field | what it is |
|---|---|
| `file` | what to `np.load(..., mmap_mode="r")` |
| `build_hash` | a digest of everything that decided the bytes |
| `build_inputs` | those things, listed — including `max_seconds`, **the ceiling the matrix was routed to** |
| `shape` | checked against `grid.n_cells` at load |
| `coverage_pct` | share of cell pairs with a route at all |
| `min_seconds`, `max_seconds` | **observed**, over reachable pairs; null when nothing is reachable |
| `departure`, `departure_window_min`, `timezone` | transit only |

The build hash covers the grid by *version*, not by content, so two bakes over
different bounding boxes under the same `grid_version` would name their files
identically. What stops that mattering is `commit_grid`: a folder's `grid.json`
is written once and verified thereafter, and a bake that would put a different
grid into it refuses and says to bump the version. The grid's digest is recorded
once per Dataset, in `manifest.grid.sha256`.

`max_seconds` means two different things in this file and the model is what
keeps them apart. `MatrixEntry.max_seconds` is the largest travel time actually
seen; `MatrixEntry.cap_seconds` (which reads `build_inputs.max_seconds`) is the
ceiling the router was given. The reader wants the second — it is the horizon it
reports when no spot is reachable — and reading the first instead is what made a
matrix of nothing but sentinels a `TypeError` on the request path rather than a
rejected bake.

Both observed values are **required but nullable**. Absent is not the same as
null: absent is a Dataset built by a code path that never filled them in, and
that is the exact file that used to raise `KeyError` while the application was
starting. `tests/fixtures/dataset/manifest-missing-max-seconds.json` is a real
one, kept as the regression.

## grid.json

`core.dataset.Grid`. Hex only: positions snap through H3, and a Dataset on a
square grid — which the producing script could also emit — was never loadable
here. H3 ids are sorted before they are numbered, so the same profile always
produces the same cell ids.

The reader builds two things out of it once, at startup: an `h3 -> id` map for
snapping a position, and a cell-id-to-coordinate array for answering with a
place.

## The matrices

`uint16` seconds, `(n_cells, n_cells)`, row-major, `65535` meaning unreachable
rather than "an hour and a half". Mapped, never read: `M[cell, :]` is one
contiguous row and the matrices total 1.4 GB, so they belong in the page cache.

Transit values are the **median** across the departure window, not the best
case. Anything shown to a person should say "typical".

## Departures

A Departure is a class of day and an hour — `transit_sat14` — and the table of
them is `core.dataset.DEPARTURES`. Adding one is a single edit: the bake builds
it and `pick_transit_key` can select it, because both read the same tuple.

A Departure becomes a *calendar date* only against a particular transit feed,
and the bake derives that date from the feed's own validity window
(`modules/matrix/feed.py`). This is deliberate, and the glossary says so: the
router reads whatever service actually ran on the date it is given, so assuming
a Departure means "a Saturday in general" is how the shipped Dataset's Saturday
Departure landed on a public holiday.

`pick_transit_key` measures distance around the clock, so 00:30 picks the 22:00
bake rather than the 08:00 one, and the class of day stays whatever the date
says it is.

## Walking is unioned into transit

A transit traveller may use `walk` as well as their transit matrix, because
riders walk to and from stops and a short hop is faster on foot than the transit
matrix admits. Note that walk's ceiling is eight hours against transit's ninety
minutes, so the union can in principle offer a transit traveller a very long
walk. Capping that is worth doing and is not part of the absorption.

## What is not here

Hot-swapping. The reader builds its `Dataset` at import, so a published Dataset
is picked up by a restart. The atomic publish is the prerequisite for doing
better, and doing better is not yet needed.

Object storage. The intended eventual home for Datasets; see ADR 0006 for what
publishing in place rules out.
