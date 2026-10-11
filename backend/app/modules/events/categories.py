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
        "festival",
        ("festival", "festivals", "openair", "open air", "open-air"),
    ),
]

_ALIAS: dict[str, Category] = {"festival": "music"}

_FALLBACK_UNSAFE = frozenset({"tour", "show", "house"})


@lru_cache(maxsize=512)
def _pattern_for(keyword: str) -> re.Pattern[str]:
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
