# mm

Finding the place a group can all reach fastest, and what is on near there.

## Language

### Meeting up

**Meetup**:
A planned gathering with a start time and a set of participants. Owned by the
account that created it.

**Participant**:
One person expected at a meetup, with a travel mode and optionally a position.
May or may not be linked to an account.
_Avoid_: member, attendee

**Rendezvous**:
The grid cell that minimises the worst participant's travel time to a meetup.
Computed on demand, never stored.
_Avoid_: meeting point, optimal spot

### Travel times

**Dataset**:
One baked folder of travel-time matrices plus its manifest and grid. Immutable
once written; identified by a grid version.

**Bake**:
The run that produces a dataset from an OSM extract and a GTFS feed.

**Cell**:
One hexagon of the H3 grid a dataset is built on. Positions are snapped to a
cell before any travel time is looked up.

**Departure**:
A specific date and time a transit matrix was computed for. Not a day of the
week — the router reads whatever service actually ran on that calendar date.

### Events

**Source**:
One site being scraped. Twenty-one of them, each with its own fetch and parse
rules and its own rate limit.

**Listing**:
One scraped record of a happening, from one source. The same real-world
happening appears as several listings when several sources cover it.
_Avoid_: raw event

**Event**:
One real-world happening on one day, after listings from every source have been
deduplicated into it. This is what a person is shown.
_Avoid_: card

**Occurrence**:
One time range of a listing, as that source published it. A source listing
seven dates gives seven; one publishing the same week as a single range gives
one, carrying its length. So the count reflects how a source expressed the
dates, not a rule of ours — anything asking what is on today has to read an
occurrence as a range rather than as a day.
_Avoid_: showing, session

**Run**:
One execution of a scrape, named so that everything it wrote can be recognised
afterwards. What a listing was last seen in is how the next run knows it is
still listed.

**Quarantine**:
Where a scraped record goes when it cannot be made into a listing — kept with
the reason and the record itself, because a source returning unusable rows is
worth knowing about and a silent drop is not.
_Avoid_: reject, invalid

**Venue**:
The place a listing happens at, as named by the source. Deciding whether two
sources mean the same venue is the hard part of matching listings: the name
decides it, and the position can only overrule the name by being far enough
away to be somewhere else.
