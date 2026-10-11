import collections
import datetime as dt
import difflib
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from ...core.contracts import Position

MIN_TITLE_JACCARD = 0.6
MIN_TITLE_RATIO = 0.86
MIN_VENUE_JACCARD = 0.34
MAX_KM_SAME_PLACE = 0.6
MAX_KM_NAME_VETO = 5.0
UMBRELLA_MAX_TOKENS = 2

_STOP = frozenset(
    """der die das den dem des und oder mit ohne von vom zu zur zum im in am an
    auf fur fuer the a of and with at on live open air tour show wien vienna
    support presented by feat featuring vol part teil abend ausverkauft
    zusatztermin zusatzkonzert tickets ticket praesentiert prasentiert""".split()  # noqa: SIM905
)


def fold(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


_YEAR = re.compile(r"^(?:19|20)\d{2}$")


def tokens(value: str | None) -> frozenset[str]:
    parts = re.split(r"[^0-9a-z]+", fold(value))
    return frozenset(
        p for p in parts if len(p) > 2 and p not in _STOP and not _YEAR.match(p)
    )


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def km_apart(a: Position | None, b: Position | None) -> float | None:
    if a is None or b is None:
        return None
    r = 6371.0
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp = p2 - p1
    dl = math.radians(b.longitude - a.longitude)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Candidate:
    listing_id: int
    source: str
    date_local: dt.date
    title: str
    title_norm: str
    venue_name: str | None = None
    city: str | None = None
    position: Position | None = None
    all_day: bool = False
    completeness: int = 0

    @property
    def title_tokens(self) -> frozenset[str]:
        return tokens(self.title)

    @property
    def venue_tokens(self) -> frozenset[str]:
        return tokens(self.venue_name)


@dataclass(frozen=True)
class Verdict:
    same: bool
    reason: str
    title_rule: str | None = None
    venue_rule: str | None = None


def _title_rule(a: Candidate, b: Candidate) -> str | None:
    if a.title_norm and a.title_norm == b.title_norm:
        return "exact"
    ta, tb = a.title_tokens, b.title_tokens
    if not ta or not tb:
        return None
    if ta <= tb or tb <= ta:
        return "subset"
    if jaccard(ta, tb) >= MIN_TITLE_JACCARD:
        return "jaccard"
    if (
        difflib.SequenceMatcher(None, a.title_norm, b.title_norm).ratio()
        >= MIN_TITLE_RATIO
    ):
        return "ratio"
    return None


def _city_conflict(a: Candidate, b: Candidate) -> bool:
    if not a.city or not b.city:
        return False
    ca, cb = fold(a.city).strip(), fold(b.city).strip()
    return bool(ca and cb and ca != cb)


def _name_agreement(a: Candidate, b: Candidate) -> str | None:
    if not a.venue_name or not b.venue_name:
        return None
    va, vb = a.venue_tokens, b.venue_tokens
    if va and vb and (va <= vb or vb <= va):
        return "containment"
    if jaccard(va, vb) >= MIN_VENUE_JACCARD:
        return "token-overlap"
    fa, fb = fold(a.venue_name).replace(" ", ""), fold(b.venue_name).replace(" ", "")
    if fa and fb and (fa in fb or fb in fa):
        return "substring"
    return None


def _venue_rule(a: Candidate, b: Candidate, distance: float | None) -> tuple[bool, str]:
    named = _name_agreement(a, b)
    if named:
        if distance is not None and distance > MAX_KM_NAME_VETO:
            return False, f"too-far:{distance:.2f}km"
        return True, named

    if distance is not None:
        if distance <= MAX_KM_SAME_PLACE:
            return True, "coords"
        return False, f"coords-conflict:{distance:.2f}km"

    if not a.venue_name or not b.venue_name:
        return True, "one-missing"
    return False, "conflict"


def compare(a: Candidate, b: Candidate) -> Verdict:
    if a.date_local != b.date_local:
        return Verdict(False, "different-day")

    if _city_conflict(a, b):
        return Verdict(False, "city-conflict")

    distance = km_apart(a.position, b.position)
    title_rule = _title_rule(a, b)
    if title_rule is None:
        return Verdict(False, "title-mismatch")

    venue_ok, venue_rule = _venue_rule(a, b, distance)
    if not venue_ok:
        return Verdict(False, f"venue-{venue_rule}", title_rule, venue_rule)

    if title_rule in ("jaccard", "ratio") and _weak_venue_evidence(a, b, venue_rule):
        # A fuzzy title match that no Venue *name* corroborates is a guess.
        # wien.gv.at publishes district programmes whose titles are mostly
        # shared boilerplate - '"Favoriten spielt" 2026 - Kostenlose
        # Veranstaltungen für Kinder…' against the identical text for Hernals -
        # and those cleared 0.6 Jaccard on the boilerplate alone, 447 times.
        #
        # Conditioned on the data, like the umbrella guard, and for the same
        # reason: the original form also required `distance is None`, so it went
        # dead the moment those rows were geocoded. Every one of them addresses
        # "Verschiedene Orte im Bezirk", which resolves to a vague point, the
        # venue rule became "coords", and four district programmes merged into
        # one group on 44 separate days - 117 wrongly collapsed rows.
        return Verdict(False, "weak-evidence", title_rule, venue_rule)

    if title_rule == "subset":
        umbrella = _umbrella_reason(a, b, venue_rule)
        if umbrella:
            return Verdict(False, umbrella, title_rule, venue_rule)

    return Verdict(True, "match", title_rule, venue_rule)


_NAME_RULES = frozenset({"containment", "token-overlap", "substring"})


def _weak_venue_evidence(a: Candidate, b: Candidate, venue_rule: str) -> bool:
    return venue_rule not in _NAME_RULES and not (a.venue_name and b.venue_name)


def _umbrella_reason(a: Candidate, b: Candidate, venue_rule: str) -> str | None:
    shorter = a if len(a.title_tokens) <= len(b.title_tokens) else b
    if not shorter.title_tokens:
        return None

    # Conditioned on the data (is there a Venue name at all?) rather than on
    # which venue rule fired. Keying it on `venue_rule == "one-missing"`
    # silently disabled this guard for geocoded rows: once both sides have
    # coordinates the rule became "coords", so "Kultursommer Wien" was free to
    # absorb every concert in the festival again - the 188-row-blob failure
    # returning as coverage grew.
    if (
        _weak_venue_evidence(a, b, venue_rule)
        and len(shorter.title_tokens) <= UMBRELLA_MAX_TOKENS
    ):
        return "umbrella-weak-venue"

    for candidate in (a, b):
        if candidate.venue_name and shorter.title_tokens <= tokens(
            candidate.venue_name
        ):
            return "umbrella-venue-season"
    return None


@dataclass
class Group:
    date_local: dt.date
    members: list[Candidate] = field(default_factory=list)
    reasons: collections.Counter = field(default_factory=collections.Counter)

    @property
    def primary(self) -> Candidate:
        return max(
            self.members,
            key=lambda c: (c.completeness, not c.all_day, c.source, c.listing_id),
        )

    @property
    def sources(self) -> list[str]:
        return sorted({m.source for m in self.members})


def group_day(candidates: list[Candidate]) -> list[Group]:
    parent = {c.listing_id: c.listing_id for c in candidates}
    matched: list[tuple[int, str]] = []

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            verdict = compare(a, b)
            if not verdict.same:
                continue
            ra, rb = find(a.listing_id), find(b.listing_id)
            if ra != rb:
                parent[rb] = ra
            matched.append((a.listing_id, f"{verdict.title_rule}/{verdict.venue_rule}"))

    buckets: dict[int, list[Candidate]] = collections.defaultdict(list)
    for c in candidates:
        buckets[find(c.listing_id)].append(c)

    # Roots move during union, so reasons are attributed only once the closure
    # is final.
    reasons: dict[int, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for listing_id, key in matched:
        reasons[find(listing_id)][key] += 1

    return [
        Group(
            date_local=members[0].date_local,
            members=members,
            reasons=reasons.get(root, collections.Counter()),
        )
        for root, members in buckets.items()
    ]


def completeness_score(
    *,
    positioned: bool = False,
    street: bool = False,
    described: bool = False,
    both_languages: bool = False,
    image: bool = False,
    priced: bool = False,
    ticket_url: bool = False,
    all_day: bool = True,
    end_known: bool = False,
) -> int:
    score = 0
    if positioned:
        score += 4
    if street:
        score += 1
    if described:
        score += 2
    if both_languages:
        score += 1
    if image:
        score += 1
    if priced:
        score += 1
    if ticket_url:
        score += 1
    if not all_day:
        score += 2
    if end_known:
        score += 1
    return score


@dataclass(frozen=True)
class Content:
    listing_id: int
    source: str
    lang_primary: str = "de"
    title_de: str | None = None
    title_en: str | None = None
    description_de: str | None = None
    description_en: str | None = None
    venue_name: str | None = None
    street: str | None = None
    postcode: str | None = None
    city: str | None = None
    position: Position | None = None
    url: str | None = None
    origin_url: str | None = None
    image_url: str | None = None
    completeness: int = 0


_GAP_FILLED = (
    "title_de",
    "title_en",
    "description_de",
    "description_en",
    "venue_name",
    "street",
    "postcode",
    "city",
    "image_url",
)


def gap_fill(primary: Content, others: Sequence[Content]) -> Content:
    ranked = sorted(others, key=lambda one: -one.completeness)
    filled: dict[str, object] = {}

    for name in _GAP_FILLED:
        if _present(getattr(primary, name)):
            continue
        for other in ranked:
            value = getattr(other, name)
            if _present(value):
                filled[name] = value
                break

    if primary.position is None:
        for other in ranked:
            if other.position is not None:
                filled["position"] = other.position
                break

    return replace(primary, **filled)


def _present(value: object) -> bool:
    return value is not None and value != ""
