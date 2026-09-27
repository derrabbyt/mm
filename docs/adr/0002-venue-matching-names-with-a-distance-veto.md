---
status: accepted
---

# Venue matching is by name, with a 5 km distance veto

Deciding whether two listings describe the same venue is done by comparing the
names the sources give it; coordinates are consulted only after every name test
has failed. On top of that, two listings are never merged when both carry a
position and those positions are more than **5 km** apart, whatever the names
say. Names decide identity, distance holds a veto.

## Why names lead

Measured over a 412-day corpus, venue-name containment carries ~83% of merges
and coordinates ~4%. That 4% is not a measure of how informative coordinates
are — it is how rarely they are *reached*, since they sit last in an ordered
ladder. What they carry is the set of cases names structurally cannot:

    (no venue name)                  ⟂  City Hall Square
    Haus der Geschichte Österreich   ⟂  House of Austrian History

3.6% of listings carry no venue name at all, and no string rule matches a German
and an English name for the same building.

The module's own docstring asserts the opposite — that coordinates are the
identity signal and names a fallback — and an alias table was deleted on the
strength of that claim. The docstring is wrong and is corrected when the module
lands here.

## Why the veto exists anyway

Name agreement alone merges venues that are plainly not the same place, and two
cases prove it:

    7.5 km   Orpheum Graz  ⟂  Orpheum Wien     the stop list strips `wien` and
                                               leaves `graz`, making one name a
                                               token subset of the other
    6.6 km   B72  ⟂  Szene Wien                two different Vienna clubs, zero
                                               shared name tokens, joined by
                                               transitive chaining

Neither is reachable by a name rule. Both are obvious by distance.

## The cost, accepted knowingly

A 5 km cutoff rejects 189 of 3,213 merges where both sides have a position —
5.9%, taking duplicate reduction from ~13.9% to roughly ~12.7%. Two classes are
lost:

- **A hall inside a building**, geocoded to the building's other entrance or to
  a district centroid: `Arena - Große Halle` ⟂ `Arena Wien` (6.7 km),
  `Musikverein Wien, Brahms-Saal` ⟂ `Wiener Musikverein` (40.9 km).
- **The same venue geocoded badly**: `Karlskirche` ⟂ `Karlskirche - Pfarre St.
  Karl Borromäus` (7.7 km), `VHS Großjedlersdorf` ⟂ itself (10.0 km),
  `Schutzhaus zur Zukunft` ⟂ itself (16.5 km).

Distance does not separate those from the true errors — the correct Musikverein
merge is 40.9 km apart, five times further than the wrong Orpheum one. So the
veto cannot be tuned to keep them; losing them is the price of catching the
errors, and a duplicate shown twice is a smaller defect than two different clubs
presented as one event.

## Where the veto does and does not reach

It touches exactly one branch: the one where the venue *names* agreed. Every
other path already had a distance check, and a stricter one — once names stop
agreeing, `_venue_rule` falls through to coordinates and rejects above **0.6
km**. So listings with a missing or malformed venue name, or with names that
simply differ, are unaffected by the 5 km rule; they were never merging over
distance in the first place.

It is also partial where two different venues resolve to the same point. `B72`
and `Szene Wien` are different clubs 6.6 km apart, but the geocoder placed some
`B72` listings at Szene Wien's coordinates. The veto cuts the 6.6 km edges and
the 0.0 km ones survive, and because groups form by transitive closure one
surviving edge holds the group together.

Two residues are accepted knowingly. `Silvester Schifffahrt - MS Austria` and
`- MS Dürnstein` are different boats leaving the same dock: coordinates agree,
the titles share a prefix, and the venue names that would settle it are ignored
once they disagree. And `_weak_venue_evidence` treats "both rows have a venue
name" as strong evidence even when those names contributed nothing, so the guard
against an uncorroborated fuzzy title does not fire there. Correcting it would
reject four pairs in the corpus, two of them correct.

## Consequences

The veto applies only when **both** listings carry a position. A listing with no
coordinates cannot be too far from anything and falls through to the name rules
unchanged — otherwise the 3.6% of listings with no venue name, which are the
ones the coordinate rung exists to serve, would be rejected for missing data.

5 km is the lowest cutoff that catches both known errors (6.6 km and 7.5 km).
There is a gap in the data between 5 km and 8 km holding 178 pairs, so the
number is not finely tuned: 8 km would reject only 11 pairs but miss both errors.

Bad geocoding still costs us elsewhere. `getRendezvousEvents` filters events by
`ST_DistanceSphere` on their stored coordinates, so a venue geocoded 40 km off
is missing from results near a rendezvous it is beside. That is a separate
problem from matching, and this decision does not address it.
