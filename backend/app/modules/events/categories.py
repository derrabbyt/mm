"""Map each Source's own taxonomy onto the canonical `Category`.

Pure data plus one lookup. `categories_raw` is always stored verbatim alongside
the canonical value, so a wrong mapping here is a re-map rather than a
re-scrape - which matters, because some upstream classifications are genuinely
odd (one Source files a hard-techno rave under "Konzert").

Matching is on a normalised substring, because the same concept is spelled many
ways across twenty-one Sources ("konzert", "concerts-and-music", "Rock, Pop,
Jazz and more", "MusicEvent").
"""

import re
from functools import lru_cache

from .scraped import Category

# Order matters: the first keyword found anywhere in the label wins, so the more
# specific category must come first.
#
# * party before music - "electronic party" is a party.
# * theatre before music - otherwise `music` matches "**Music**al, Dance and
#   Performance" and files a musical under music. Verified against wien.info's
#   real taxonomy.
# * theatre before art - "Theater und Kabarett" must not land in art via "kunst".
_RULES: list[tuple[Category, tuple[str, ...]]] = [
    (
        "party",
        (
            # "parties" is listed separately: `\bparty` does not match "Parties",
            # which appears in wien.info's "Festivals, Parties, and Shows".
            "party",
            "parties",
            "clubbing",
            "rave",
            "techno",
            "house",
            "electronic",
            "elektronisch",
            "disco$",
            "disko",
            "dj",
            "nightlife",
            "clubnacht",
            "ball$",
            "after work",
            "afterwork",
            "clubbings",
        ),
    ),
    (
        "theatre",
        (
            "theater",
            "theatre",
            "kabarett",
            "cabaret",
            "musical",
            "buehne",
            "bühne",
            "improv",
            "comedy",
            "stand-up",
            "standup",
            "performance",
            "shows-and-performances",
            "show",
            "varieté",
            "variete",
            "tanz",
            "dance",
            "ballet",
            "ballett",
            "circus",
            "zirkus",
            "puppen",
            # Multi-word, so safe in free text where bare "show" is not.
            "variety show",
            "dinner show",
            "salsa",
            "bachata",
            "tango",
        ),
    ),
    (
        "music",
        (
            "konzert",
            "concert",
            "musik",
            "music",
            "klassik",
            "classical",
            "oper",
            "opera",
            "operette",
            "jazz",
            "rock",
            "pop",
            "chor",
            "musicevent",
            "musikfestival",
            "singer",
            "songwriter",
            "hip hop",
            "hiphop",
            "dnb",
            "gig",
            "live music",
            "livemusik",
            "philharm",
        ),
    ),
    (
        "art",
        (
            "ausstellung",
            "exhibition",
            "kunst",
            "art",
            "museum",
            "galerie",
            "gallery",
            "vernissage",
            "design",
            "kultur",
            "culture",
            "installation",
            "fotografie",
            "photography",
        ),
    ),
    ("film", ("film", "kino", "cinema", "movie", "screening", "dokumentar")),
    (
        "family",
        (
            "kinder",
            "children",
            "family",
            "familie",
            "jugend",
            "kids",
            "children-and-families",
            "kinderuni",
        ),
    ),
    (
        "food",
        (
            "kulinar",
            "culinary",
            "food",
            "drink",
            "wein",
            "wine",
            "beer",
            "bier",
            "brunch",
            "degustation",
            "weinfest",
            "heurige",
            "food-and-drinks",
            "essen",
            "kaffee",
            "cocktail",
            "tasting",
        ),
    ),
    (
        "sport",
        (
            "sport",
            "lauf",
            "run$",
            "running",
            "marathon",
            "yoga",
            "fitness",
            "workout",
            "radfahren",
            "cycling",
            "schwimm",
            "turnier",
            "tournament",
            "match",
            "fussball",
            "football",
            "basketball",
        ),
    ),
    (
        "market",
        (
            "markt",
            "market",
            "messe",
            "fair$",
            "flohmarkt",
            "fleamarket",
            "kirtag",
            "bazaar",
            "trade",
            "market-messe",
            "christkindlmarkt",
        ),
    ),
    (
        "talk",
        (
            "vortrag",
            "talk",
            "lesung",
            "reading",
            "spoken",
            "spoken-word",
            "diskussion",
            "discussion",
            "workshop",
            "seminar",
            "konferenz",
            "conference",
            "kongress",
            "führung",
            "fuehrung",
            "guided",
            "tour",
            "stadtspaziergang",
            "spaziergang",
            "poetry",
            "slam",
            "quiz",
            "pubquiz",
            "trivia",
            "podcast",
            "literatur",
            "literature",
            "buchpräsentation",
            "buchpraesentation",
        ),
    ),
    (
        "festival",  # folded into music/other below; kept for keyword clarity
        ("festival", "festivals", "openair", "open air", "open-air"),
    ),
]

# `festival` is not a canonical category (a festival is music/art/film in
# practice); map it to music, which is what the overwhelming majority are.
_ALIAS: dict[str, Category] = {"festival": "music"}

# Not every label is a category. wien.gv.at tags accessibility and funding
# ("Barrierefreier Zugang" ×43, "Förderung" ×24) in the same field as topics;
# those correctly fall through to "other" and must not be mapped.
#
# Keywords that are trustworthy inside a curated taxonomy label but not inside a
# free-text title, so `fallback_text` matching skips them. Measured against the
# real corpus:
#
# * `tour`  - 137 distinct titles, nearly all band tours ("SHINDY – Blüte 2
#   Tour"), filed as talk. As a label it means "Guided Tours and Round Trips".
# * `show`  - "EP Release Show" and "anniversary show" are music, not theatre.
#   Real theatre titles still match via comedy/kabarett/musical/variety show.
# * `house` - "GRANDMAS HOUSE", "HOUSE OF CARDS", "Open House Wien" are not
#   parties. As a label it is the music genre ("Tech House", "Afro House").
_FALLBACK_UNSAFE = frozenset({"tour", "show", "house"})


@lru_cache(maxsize=512)
def _pattern_for(keyword: str) -> re.Pattern[str]:
    """Match a keyword at a word start, but allow anything after it.

    German compounds force this shape. A plain substring match is wrong -
    `run` matches "füh**run**gen" (guided tours), classifying a walking tour as
    sport. A fully word-bounded match is equally wrong - `\\bmusik\\b` misses
    "Musikveranstaltung". Anchoring only the *start* of the keyword satisfies
    both: `\\bmusik` matches the compound, `\\brun` does not match inside
    "führungen".

    A keyword ending in `$` opts into a **whole-word** match, for the few that
    are prefixes of unrelated words - `ball` would otherwise claim "Ballett".
    """
    if keyword.endswith("$"):
        return re.compile(rf"\b{re.escape(keyword[:-1])}\b", re.IGNORECASE)
    return re.compile(rf"\b{re.escape(keyword)}", re.IGNORECASE)


def _canonical_for_label(label: str, free_text: bool = False) -> Category | None:
    for category, keywords in _RULES:
        for keyword in keywords:
            if free_text and keyword in _FALLBACK_UNSAFE:
                continue
            if _pattern_for(keyword).search(label):
                return _ALIAS.get(category, category)  # type: ignore[arg-type]
    return None


def to_canonical(
    labels: list[str] | None,
    fallback_text: str | None = None,
) -> Category:
    """Pick one canonical category from a Source's raw labels.

    Earlier labels win, because Sources generally list their primary category
    first (events.at, rausgegangen and wien.info all do).

    `fallback_text` - the **title only** - is consulted when no label matched.
    Several Sources publish no taxonomy at all (wien_ticket, eventfinder,
    event_spotter and events_at account for ~800 of these), and inferring "Rock
    the Opera" as music from its title beats filing it under "other". Because
    `categories_raw` stays empty in that case, an inferred category is always
    distinguishable from a declared one.

    Passing the **description** was tried and rejected. It halved `other`
    again, but wrongly: a blurb long enough to contain some keyword almost
    always contains one from a high-priority rule, so rule order stopped meaning
    "more specific" and started meaning "listed first". 357 punk and metal
    concerts became theatre via "die Show", tours became talk, and "MAITE KELLY
    LIVE" became food. A title is a deliberate label; a blurb is prose.
    """
    for label in labels or ():
        if not label:
            continue
        found = _canonical_for_label(str(label))
        if found:
            return found

    if fallback_text:
        found = _canonical_for_label(str(fallback_text), free_text=True)
        if found:
            return found
    return "other"
