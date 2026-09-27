"""Group the Listings that describe the same real-world happening on one day.

Values in, values out: candidate Listings in, groups out. No session and no
network, so a wrong merge is reproducible in a test from the two rows that
caused it, and a bad rule is a re-run rather than a re-scrape. Nothing here
deletes or rewrites a Listing; a grouping is recorded beside them.

**Names decide identity, and distance holds a veto** - see
docs/adr/0002-venue-matching-names-with-a-distance-veto.md. Measured over a
412-day corpus, Venue names settle ~88% of merges (containment alone ~83%) and
coordinates ~4%; two Listings are nonetheless never merged when both carry a
position and those positions are more than `MAX_KM_NAME_VETO` apart, whatever
the names say.

The scraper this came from documented the reverse - that coordinates were the
identity signal and names only a fallback - and a table of 32 Vienna Venue
spellings was deleted on the strength of that claim. The corpus says otherwise.
Coordinates are rarely *reached*, because they sit last in an ordered ladder;
what they carry is the set of cases names structurally cannot, a Listing with
no Venue name at all and a German name against its English translation.

Four more things were measured on the corpus before any rule was written, and
each one shapes the design:

1. **Group per day, never across days.** Sources disagree on what a happening
   is: wien_ticket emits one Listing *per performance* (188 for the *Maria
   Theresia* run), while events_at emits one per *production* carrying many
   Occurrences. Clustering transitively across dates chained all 188 into a
   single blob. The unit is therefore `(group, day)`.

2. **Venue names disagree systematically across languages.** wien.info
   publishes English, everything else German, so the same building arrives as
   "Karlskirche" and "St. Charles' Church". 3.6% of Listings carry no Venue
   name at all. Those are what the coordinate rung is for.

3. **Start times cannot be part of the key.** wien.info and events_at emit
   `00:00` for "time unknown", and Sources genuinely disagree on real times -
   "Jam & Picknick" is 14:00 and 18:00, "Anette Olzon" 19:00 (doors) and 22:00
   (stage). Same happening both times.

4. **Festival umbrellas must not swallow their programme.** "Kultursommer Wien"
   is a token subset of "10 Jahre ASAGAN - Kultursommer Wien" and of "Waltzing
   in the Clouds - Kultursommer Wien", which are different concerts. Same
   class: "Sport.Platz Wien" across five parks, "Gürtel Nightwalk" across six
   bars.

Every threshold the matching rules turn on is stated once in the block below;
what counts as a token at all is stated once in `tokens`.
"""

import collections
import datetime as dt
import difflib
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from ...core.contracts import Position

# A pair whose titles overlap this much is the same happening even when the
# wording differs ("WAR ON WOMEN (USA) / ADAM BOMB (USA)" ~ "MJ Presents: War on
# Women & Adam Bomb").
MIN_TITLE_JACCARD = 0.6
# Character-level similarity, for near-identical spellings that tokenise apart
# ("Wiener Mozart Konzert" ~ "Wiener Mozart Konzerte").
MIN_TITLE_RATIO = 0.86
# Venue token overlap that counts as the same place.
MIN_VENUE_JACCARD = 0.34
# Distance does two different jobs, so it needs two thresholds.
#
# `MAX_KM_SAME_PLACE` decides identity when the Venue *names* disagree - the job
# the deleted alias table used to do. Measured on 1,183 title-matched
# cross-Source pairs that are both geocoded, the distribution has a clear
# valley: 73% sit within 100 m (280 of them identical to the metre), 7% more
# within 250 m, then almost nothing until a separate population starts at 1 km.
# It has to stay under the 1.14 km between Karlskirche and Stephansdom, which run
# the same Vivaldi programme on the same nights.
MAX_KM_SAME_PLACE = 0.6
# `MAX_KM_NAME_VETO` overrules an agreeing name. Name agreement alone merges
# Venues that are plainly not the same place - "Orpheum Graz" against "Orpheum
# Wien" 7.5 km apart, because the stop list strips `wien` and leaves `graz` -
# and no name rule can reach those. 5 km is the lowest cutoff that catches both
# known errors (6.6 km and 7.5 km); the data has a gap between 5 km and 8 km
# holding 178 pairs, so the number is not finely tuned. It rejects 5.9% of the
# merges where both sides have a position, which is knowingly accepted: a
# duplicate shown twice is a smaller defect than two different clubs presented
# as one happening. See ADR 0002 for the correct merges this loses.
MAX_KM_NAME_VETO = 5.0
# A subset match on a title this short is an umbrella, not an identity (see 4).
UMBRELLA_MAX_TOKENS = 2

# Words that carry no identity: decoration, boilerplate, or the city itself.
# Every one was observed defeating a match or creating a false one. Written as
# prose rather than fifty quoted strings, because it is read and edited as
# words.
_STOP = frozenset(
    """der die das den dem des und oder mit ohne von vom zu zur zum im in am an
    auf fur fuer the a of and with at on live open air tour show wien vienna
    support presented by feat featuring vol part teil abend ausverkauft
    zusatztermin zusatzkonzert tickets ticket praesentiert prasentiert""".split()  # noqa: SIM905
)


def fold(value: str | None) -> str:
    """Casefold and strip accents. The comparison space for everything here."""
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


_YEAR = re.compile(r"^(?:19|20)\d{2}$")


def tokens(value: str | None) -> frozenset[str]:
    """Significant tokens of a title or Venue name.

    Tokens shorter than three characters are dropped along with `_STOP`. That is
    deliberately blunt: it is what makes "Carpenter Brut" match "CARPENTER BRUT
    (fra) + HEALTH (us) *OPEN AIR*".

    Years are dropped too. Every happening this season says "2026", so keeping
    them inflates similarity between unrelated ones: with years counted,
    "Kultursommer Wien 2026" and "Alsergrunder Kultursommer 2026" share two of
    three tokens and match. That one omission moved the Jaccard rule from 5
    firings to 371.
    """
    parts = re.split(r"[^0-9a-z]+", fold(value))
    return frozenset(
        p for p in parts if len(p) > 2 and p not in _STOP and not _YEAR.match(p)
    )


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def km_apart(a: Position | None, b: Position | None) -> float | None:
    """Great-circle distance in km, or None unless both sides are positioned.

    None is the answer the veto turns on: a Listing with no position cannot be
    too far from anything.
    """
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
    """One Listing on one day, as the matcher sees it.

    A plain value rather than a row: `title_norm` is the form normalisation
    already stored, and `completeness` is what `completeness_score` made of the
    Listing's content, so nothing here has to read a column.
    """

    listing_id: int
    source: str
    date_local: dt.date
    title: str
    title_norm: str
    venue_name: str | None = None
    city: str | None = None
    # Null for the Listings nothing could be geocoded from, which is what makes
    # the distance veto inapplicable to them rather than fatal.
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
    """Why a pair did or did not match.

    Returned rather than a bool so a surprising grouping can be explained
    without re-deriving it.
    """

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
    """Do both Listings name a city, and disagree?"""
    if not a.city or not b.city:
        return False
    ca, cb = fold(a.city).strip(), fold(b.city).strip()
    return bool(ca and cb and ca != cb)


def _name_agreement(a: Candidate, b: Candidate) -> str | None:
    """Do the two Venue names describe the same place? None if they disagree."""
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
    """Same place? Names decide it, and distance can only veto.

    Names first, and a positive name match survives geocoder scatter: "The
    Comedy Pub" and "The Comedy Pub, Wien" resolved 0.96 km apart, and "Bellini
    Hall" against "Bellini Hall, Wien" 1.12 km - the same Venue, geocoded from
    two query strings. Letting `MAX_KM_SAME_PLACE` overrule the name there split
    real duplicates, which is why only the far looser veto can.

    The veto needs both sides positioned. A Listing with no coordinates cannot
    be too far from anything and falls through to the name rules unchanged -
    otherwise the Listings with no Venue name, the ones the coordinate rung
    exists to serve, would be rejected for missing data.

    Coordinates decide the cases the names could not reach at all: "Karlskirche"
    and "St. Charles' Church" resolve to the same point, so nothing has to know
    they are synonyms. That is what replaced the hand-curated alias table of 32
    Vienna Venue spellings - a manual list, fitted to one snapshot, that would
    have rotted silently as Sources changed wording.
    """
    named = _name_agreement(a, b)
    if named:
        if distance is not None and distance > MAX_KM_NAME_VETO:
            return False, f"too-far:{distance:.2f}km"
        return True, named

    # Names disagree, or one is missing: coordinates have to vouch for the place.
    if distance is not None:
        if distance <= MAX_KM_SAME_PLACE:
            return True, "coords"
        return False, f"coords-conflict:{distance:.2f}km"

    if not a.venue_name or not b.venue_name:
        return True, "one-missing"
    return False, "conflict"


def compare(a: Candidate, b: Candidate) -> Verdict:
    """Are these two Listings the same real-world happening on this day?"""
    if a.date_local != b.date_local:
        return Verdict(False, "different-day")

    # Cities are stated by the Source rather than inferred, so a conflict is
    # trustworthy in a way a distance is not. This is what keeps a touring show
    # apart when both Venues happen to share a name ("Wiener Stadthalle"
    # against "Stadthalle Wels").
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


# Venue rules that positively vouch for the place because the *names* agree. The
# umbrella and weak-evidence guards trust these; neither trusts coordinates or a
# missing name.
_NAME_RULES = frozenset({"containment", "token-overlap", "substring"})


def _weak_venue_evidence(a: Candidate, b: Candidate, venue_rule: str) -> bool:
    """Did anything but a Venue *name* vouch for these two being the same place?

    Coordinates do not count. A Listing with no Venue name is geocoded from
    whatever address it has, and the ones that lack a name tend to lack a real
    address too - so their coordinates say "somewhere in this district", not
    "this building".

    A residue is accepted here knowingly: "both rows have a Venue name" counts
    as strong evidence even when those names contributed nothing to the match,
    so the guard against an uncorroborated fuzzy title does not fire for a pair
    whose names merely failed to agree. That is how "Silvester Schifffahrt - MS
    Austria" and "- MS Dürnstein", two different boats leaving the same dock,
    stay merged: their coordinates agree, their titles share a prefix, and the
    Venue names that would settle it are ignored once they disagree. Correcting
    it would reject four pairs in the corpus, two of them correct - see ADR
    0002.
    """
    return venue_rule not in _NAME_RULES and not (a.venue_name and b.venue_name)


def _umbrella_reason(a: Candidate, b: Candidate, venue_rule: str) -> str | None:
    """Is the shorter title a programme umbrella rather than an identity?

    A subset match says "one title contains the other", which is usually the
    same happening described at different lengths. Two cases where it is not:

    * **Festival umbrella.** "Kultursommer Wien" is a subset of "10 Jahre ASAGAN
      - Kultursommer Wien" and of "Waltzing in the Clouds - Kultursommer Wien",
      which are different concerts in the same festival. Same shape:
      "Sport.Platz Wien" across five parks, "Gürtel Nightwalk" across six bars.
      Guarded when the umbrella is short and no Venue *name* corroborates the
      match - coordinates do not count here, because a festival's concerts share
      their coordinates.

      Two alternatives were measured and rejected. Blocking on weak venue
      evidence alone cost 18 genuine merges ("TJARK" ~ "TJARK @ Szene Wien",
      "Civo" ~ "CIVO - Bis zum Mond und zurück") for no gain on current data.
      Scoring how many distinct titles a short title is a subset of ("fan-out")
      does not discriminate either: "VIVALDI" reaches 6 and "Beatpatrol" 4, both
      genuine, against "Kultursommer Wien" at 5.

    * **Venue season.** "Theater im Park 2026" and "Theater im Park am Belvedere
      2026" are the Venue's whole summer programme, and they were merging into
      individual concerts there ("Max Müller", "Der Nino aus Wien"). The tell is
      that the title says nothing the Venue name does not already say.
    """
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
    """The Listings one Event will be built from: one day, one happening.

    Not an Event itself. What an Event is made of is decided here - which
    Listings belong together, which of them represents them, and what it shows
    (see `gap_fill`) - but what an Event *is*, as a row someone can read, is
    the module's to know and not this file's.
    """

    date_local: dt.date
    members: list[Candidate] = field(default_factory=list)
    # Which title/venue rule pairs built the group, for explaining it afterwards.
    reasons: collections.Counter = field(default_factory=collections.Counter)

    @property
    def primary(self) -> Candidate:
        """The member an Event should be built around.

        Chosen by **field completeness**, not a hand-ranked Source list: the
        richest Listing wins, ties broken deterministically. A Source ranking
        would need maintaining every time a Source improves or degrades;
        completeness tracks that by itself.
        """
        return max(
            self.members,
            key=lambda c: (c.completeness, not c.all_day, c.source, c.listing_id),
        )

    @property
    def sources(self) -> list[str]:
        return sorted({m.source for m in self.members})


def group_day(candidates: list[Candidate]) -> list[Group]:
    """Cluster one day's candidates by transitive closure over matching pairs.

    Transitivity is wanted here: five Sources describing the Carpenter Brut gig
    only pairwise-match some of the ten combinations ("Carpenter Brut" ~
    "Carpenter Brut @ Arena Wien" ~ "CARPENTER BRUT (fra) + HEALTH (us)"), and
    the closure recovers the single real happening. It is safe **because** the
    grouping is confined to one day and guarded by the distance veto and the
    umbrella rule; without those, closure was what produced the 188-row blob.

    A group survives on any one remaining edge, so where the geocoder put two
    different Venues on the same point the veto cuts the edges that disagree and
    the 0 km ones hold the group together. That residue is known and accepted.
    """
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
    """How much usable content a Listing carries. Drives primary selection.

    Weighted towards what the site cannot do without: a position (it plots the
    happening and filters by distance from a Rendezvous) and a real start time
    (wien.info publishes `00:00` for everything, which sorts wrong and reads
    worse). Flags rather than a row, so the weights are the only thing here and
    the caller says what its columns mean.
    """
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
    """One Listing's share of an Event: what a person is shown, per Source.

    Separate from `Candidate` because the two answer different questions. A
    Candidate is what the matcher compares; a Content is what survives into the
    Event once the matching is over, which is why the fields barely overlap.
    """

    listing_id: int
    source: str
    # Which of the two title/description sides this Source actually filled.
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
    # The Source's own page, and the organiser's where the Source named one.
    url: str | None = None
    origin_url: str | None = None
    image_url: str | None = None
    completeness: int = 0


# Filled from another Listing when the primary has nothing. Only fields that
# describe the happening: the identity ones - the Listing id, its Source and its
# links - stay the primary's, so an Event always points at one real Listing
# rather than at the best of everyone's. `position` is absent because it is
# filled whole rather than field by field.
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
    """The Event's content: the primary Listing, gap-filled from the rest.

    Needed because the primary is chosen on overall completeness, which is
    dominated by the position - and the Listing that has a position is not
    always the one that has a Venue name. The "Afrika Tage" Event rendered as
    "@ ?" because wien.gv.at won on coordinates while carrying no Venue name,
    though four other Sources named the Donauinsel.

    Only gaps are filled, never overwritten: where two Sources disagree the
    primary wins, so an Event stays internally consistent rather than becoming
    a best-of composite nobody published.
    """
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

    # A position is one value, not two columns: taking a latitude from one
    # Source and a longitude from another would invent a place no Source
    # reported.
    if primary.position is None:
        for other in ranked:
            if other.position is not None:
                filled["position"] = other.position
                break

    return replace(primary, **filled)


def _present(value: object) -> bool:
    """Normalisation writes the unused side of a de/en pair as "" rather than
    NULL, so an empty string is a gap like any other."""
    return value is not None and value != ""
