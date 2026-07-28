"""Redis key layout.

Every key used by the app is built here so naming exists in exactly one place -
invalidation bugs come from keys being built two different ways.

Layout:
  person:{id}   -> JSON blob of a Person (a hash would fragment the nested Position)
  people:ids    -> SET of known ids, so we can list without KEYS/SCAN
  people:seq    -> INCR counter used to mint ids
"""

PERSON_PREFIX = "person:"
PEOPLE_IDS = "people:ids"
PEOPLE_SEQ = "people:seq"


def person(person_id: str) -> str:
    return f"{PERSON_PREFIX}{person_id}"
