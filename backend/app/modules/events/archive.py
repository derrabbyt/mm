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

_TZ = ZoneInfo(VIENNA_TZ)


def _safe(value: str) -> str:
    return _UNSAFE.sub("-", value.lower()).strip("-") or "payload"


def _day_dir(source: str, day: dt.date) -> Path:
    if not _SAFE_NAME.match(source):
        raise ValueError(f"unusable Source name for an archive path: {source!r}")
    return settings.scrape_archive_dir / source / day.isoformat()


def _digest(payload: RawPayload) -> str:
    key = json.dumps(
        [payload.url, payload.kind, payload.meta], sort_keys=True, default=str
    )
    return hashlib.sha256(key.encode() + b"\x00" + payload.body).hexdigest()[:16]


def store(payload: RawPayload, *, source: str) -> Path | None:
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
        logger.warning("could not archive %s for %s: %s", payload.url, source, exc)
        return None
    return body_path


def payloads_for(source: str, day: dt.date | None = None) -> Iterator[RawPayload]:
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
