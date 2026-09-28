"""Keeping what a site actually served.

A parse is a pure function from a document to Listings, which is what makes a
broken parser fixable - but only if the document that broke it still exists. The
committed fixtures under `tests/fixtures/` are the tripwire; this is the evidence
when the tripwire fires and the site has already moved on. By the time anyone
looks, the page that produced the nonsense is usually gone.

So every payload a run fetches is written here, gzipped, under the Source that
fetched it and the day it was fetched, next to a sidecar holding everything else
`parse` is handed. The sidecar is not optional detail: several Sources read their
`meta` rather than their body for something essential - meinbezirk takes the
queried day from it, because the cards carry no year, and wien_gv_at takes the
coordinates the index gave. A replay that dropped the meta would yield nothing
while looking exactly like a parser that had broken.

Nothing here touches the database, and nothing here can fail a run: a Source that
fetched and parsed perfectly well must not be marked failed because a disk filled
up. A failed write costs the evidence and nothing else.

The archive is local disk on purpose. Object storage is the eventual home and is
deliberately not this.
"""

import datetime as dt
import gzip
import hashlib
import json
import logging
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ...core.config import settings
from .scraped import VIENNA_TZ
from .sources.spec import RawPayload

logger = logging.getLogger(__name__)

# A Source's name reaches the filesystem as a directory, so it is checked rather
# than trusted. Sources are discovered from module names and none of them could
# contain a separator today, which is exactly the kind of thing that stays true
# until someone adds a Source.
_SAFE_NAME = re.compile(r"^[a-z0-9_]+$")
_UNSAFE = re.compile(r"[^a-z0-9_]+")
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A payload is filed under the day the run fetched it, in the run's own
# calendar. Everything else about a scrape - the window it asks a Source for,
# the window normalisation accepts - is reckoned in Vienna's, and an archive
# named in a different one would expire on a boundary nothing else observes.
_TZ = ZoneInfo(VIENNA_TZ)


def _safe(value: str) -> str:
    """A `kind` fit to be part of a filename.

    Like a Source's name, a payload's kind is free text that a Source chooses -
    but unlike the name it only labels the file, so an awkward one is worth
    flattening rather than refusing.
    """
    return _UNSAFE.sub("-", value.lower()).strip("-") or "payload"


def _day_dir(source: str, day: dt.date) -> Path:
    if not _SAFE_NAME.match(source):
        raise ValueError(f"unusable Source name for an archive path: {source!r}")
    return settings.scrape_archive_dir / source / day.isoformat()


def _digest(payload: RawPayload) -> str:
    """A name for this payload that two different payloads cannot share.

    Content-addressed, and that is the whole design. The scrape runs hourly, so
    naming a payload after where it came from means each run overwrites the
    last, and the 14:00 document that broke a parser is gone by 15:00 - exactly
    the document this archive exists to keep. Naming it after what it *is*
    keeps every version that differed and costs one copy for every version that
    did not, which is most of them.

    The URL and the meta are in the key as well as the body. Keyed on the body
    alone, two days of meinbezirk that happened to render identically would
    share one file and one sidecar, and the replay would read the wrong day out
    of it - meinbezirk takes the queried day from its meta, because the cards
    carry no year.
    """
    key = json.dumps(
        [payload.url, payload.kind, payload.meta], sort_keys=True, default=str
    )
    return hashlib.sha256(key.encode() + b"\x00" + payload.body).hexdigest()[:16]


def store(payload: RawPayload, *, source: str) -> Path | None:
    """Archive one payload. Returns where it went, or None if it could not.

    None rather than an exception: see the module docstring. The caller is in
    the middle of a scrape and has nothing useful to do about a full disk.
    """
    try:
        directory = _day_dir(source, payload.fetched_at.astimezone(_TZ).date())
        body_path = directory / f"{_safe(payload.kind)}-{_digest(payload)}.gz"
        directory.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(gzip.compress(payload.body))
        body_path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "url": payload.url,
                    "kind": payload.kind,
                    "fetched_at": payload.fetched_at.isoformat(),
                    "meta": payload.meta,
                },
                default=str,
            ),
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        # ValueError too, and that is the point of catching here rather than
        # letting `_day_dir` refuse: a Source's name is free text on its SPEC,
        # so an unusable one is a mistake in a Source rather than in the run,
        # and it must cost the evidence rather than the whole scrape.
        logger.warning("could not archive %s for %s: %s", payload.url, source, exc)
        return None
    return body_path


def payloads_for(source: str, day: dt.date | None = None) -> Iterator[RawPayload]:
    """Every archived payload for a Source, ready to hand to its `parse`.

    A bag of documents, not a recording of a run: payloads are named by what
    they are, so the order they come back in is stable but is not the order they
    were fetched in. Nothing replaying an archive needs that order - a parse is
    a pure function of one payload - and promising it would mean reading every
    sidecar before yielding the first page.

    Yields nothing for a Source that was never archived, rather than raising:
    "we do not have that document" is an answer, and the caller asking is
    already investigating something else.
    """
    root = settings.scrape_archive_dir / source
    days = [_day_dir(source, day)] if day else sorted(_subdirectories(root))

    for directory in days:
        for body_path in sorted(directory.glob("*.gz")):
            restored = _read(body_path)
            if restored is not None:
                yield restored


def _read(body_path: Path) -> RawPayload | None:
    sidecar = body_path.with_suffix(".json")
    try:
        described: dict[str, Any] = json.loads(sidecar.read_text(encoding="utf-8"))
        body = gzip.decompress(body_path.read_bytes())
    except (OSError, ValueError, KeyError) as exc:
        # Half a write is not a payload. Without the sidecar there is no kind
        # and no meta, and a parse handed the wrong kind yields silence - which
        # would read as a broken parser rather than as a broken archive.
        logger.warning("skipping unreadable archived payload %s: %s", body_path, exc)
        return None

    return RawPayload(
        url=described["url"],
        body=body,
        kind=described.get("kind", "listing"),
        fetched_at=dt.datetime.fromisoformat(described["fetched_at"]),
        meta=described.get("meta") or {},
    )


def _subdirectories(root: Path) -> Iterator[Path]:
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            yield entry


def prune(*, retention_days: int, today: dt.date) -> int:
    """Remove archived days older than the retention. Returns how many went.

    Whole days at a time, and only directories named like one: the archive sits
    on a path an operator configured, so this deletes what it recognises and
    steps over anything else it finds there.
    """
    cutoff = today - dt.timedelta(days=retention_days - 1)
    removed = 0

    for source_dir in _subdirectories(settings.scrape_archive_dir):
        for day_dir in _subdirectories(source_dir):
            if not _DAY_DIR.match(day_dir.name):
                continue
            try:
                day = dt.date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            if day >= cutoff:
                continue
            try:
                shutil.rmtree(day_dir)
            except OSError as exc:
                logger.warning("could not expire %s: %s", day_dir, exc)
                continue
            removed += 1

    if removed:
        logger.info(
            "archive: expired %d day(s) older than %s", removed, cutoff.isoformat()
        )
    return removed
