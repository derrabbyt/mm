# Auth & database patterns

How to get the current user, protect an endpoint, add a table, and query it —
using the conventions already established by `accounts`.

## 1. Getting the current user

`CurrentIdentityDep` (`app/modules/accounts/identity.py`) gives you a `SupabaseIdentity` —
the **verified JWT identity**: `supabase_user_id`, `email`, raw `claims`. It
is not a database row; `get_current_identity` rebuilds it from scratch on
every request.

Almost every authenticated endpoint wants the actual `Account` row (`id`,
`display_name`, `providers`, etc.) rather than just the claims, so that's
wrapped as its own dependency, `CurrentAccountDep` (`app/modules/accounts/service.py`):

```python
def get_current_account(identity: CurrentIdentityDep, db: DbSessionDep) -> Account:
    return get_or_create_account(db, identity)

CurrentAccountDep = Annotated[Account, Depends(get_current_account)]
```

It lives in `accounts/service.py`, not next to `CurrentIdentityDep` in
`accounts/identity.py` — `service.py` already imports `SupabaseIdentity`
from `identity.py`, so defining `CurrentAccountDep` inside
`identity.py` instead (importing `get_or_create_account` back from
`service.py`) would be a circular import. Any dependency that
composes a service function with an auth dependency belongs in the service
module; the same reasoning applies for a future `CurrentTripDep` or similar.

`accounts/router.py` uses it directly — one dependency, no manual call to
the service function:

```python
def get_my_account(account: CurrentAccountDep) -> AccountRead:
    return AccountRead.model_validate(account)
```

Any new endpoint just asks for the account the same way:

```python
def create_trip(data: CreateTripRequest, account: CurrentAccountDep, db: DbSessionDep) -> TripRead:
    ...
```

That one parameter gets you: token verified, account row loaded (or created
on first sight), all before the function body runs.

## 2. Protecting endpoints

**Public** (the default): don't ask for `CurrentIdentityDep` / `CurrentAccountDep`
at all.

**Protected, one route** — add the dependency as a parameter, same as
`accounts/router.py`:

```python
def get_my_trips(account: CurrentAccountDep, db: DbSessionDep) -> list[TripRead]:
    ...
```

FastAPI resolves dependencies *before* the function body runs. If the token
is missing or invalid, `get_current_identity` raises and the endpoint body
never executes — there is no `if not authenticated: return 401` to write.

**Protected, whole router at once** — for something like a future `trips`
router where every endpoint needs a user:

```python
router = APIRouter(
    prefix="/api/trips",
    tags=["trips"],
    dependencies=[Depends(get_current_identity)],  # enforced on every route below
    responses=default_responses(),
)

@router.get("/")
def list_trips(account: CurrentAccountDep, db: DbSessionDep) -> list[TripRead]:
    ...
```

Nuance: `dependencies=[Depends(get_current_identity)]` at the router level
*enforces* auth on every route in it, but doesn't hand you the value — you
still declare `account: CurrentAccountDep` as a parameter wherever the object
is actually needed. The router-level line is only for routes that need the
gate but not the value (rare). Normally, just put `CurrentAccountDep` on each
route and skip the router-level line — it does both.

## 3. Adding a new DB table

A table belongs to exactly one module. Everything for it lives in that
module's folder — see `docs/architecture.md` for why.

1. **Model** — `app/modules/trips/models.py`, same shape as
   `accounts/models.py` (`Base`, `Mapped`/`mapped_column`; the naming
   convention is already global via `Base.metadata`). Its wire shapes go in
   `app/modules/trips/schemas.py`.
2. **Register it** in `app/metadata.py` — this import is the
   easy-to-forget step. Alembic only compares tables it has actually imported
   into `Base.metadata`; a model that exists but was never imported there is
   invisible to autogenerate and won't be created. `migrations/env.py` imports
   `metadata` and nothing else, so this is the only place to touch.

   ```python
   # app/metadata.py
   from ..modules.accounts.models import Account
   from ..modules.trips.models import Trip

   __all__ = ["Account", "Trip"]
   ```

3. **Generate, review, apply** — containers running (`docker compose up -d`):

   ```bash
   alembic revision --autogenerate -m "create trips"
   # open the generated file in migrations/versions/, read it
   alembic upgrade head
   ```

Always read the generated migration before applying. Unfiltered autogenerate
tried to drop 40+ PostGIS/tiger tables the first time it ran against this
database, because they were visible via `search_path` but not in our
metadata — the `include_object` filter in `env.py` handles that specific
case now, but autogenerate can still misfire on renames (it sees a drop plus
an add, instead of a rename) or anything else it can't infer. Always eyeball
the diff.

## 4. Calling them — the layering

```
modules/<x>/router.py      thin HTTP glue: pulls deps, calls a service, returns
modules/<x>/public.py      what other modules may import - and all they may
modules/<x>/service.py     business logic and orchestration, takes `db: Session`
modules/<x>/repository.py  every SQLAlchemy statement, and nothing else
modules/<x>/models.py      the SQLAlchemy tables
modules/<x>/schemas.py     the Pydantic shapes on the wire
core/contracts.py          the shapes other modules see instead
```

Two rules hold this together:

- **Routers never touch SQLAlchemy**, the same way `accounts/router.py` never
  calls `get_or_create_account` itself; it only ever goes through
  `CurrentAccountDep`.
- **`SQLAlchemyError` never leaves `repository.py`.** The repository catches it
  and raises the domain error (`MeetupLoadError`, `EventsLoadError`, …), so a
  service reads as business logic rather than as error plumbing.

`modules/trips/repository.py`:

```python
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import TripsLoadError
from .models import Trip


def list_for_account(db: Session, account_id: uuid.UUID) -> Sequence[Trip]:
    try:
        return db.scalars(select(Trip).where(Trip.account_id == account_id)).all()
    except SQLAlchemyError as exc:
        raise TripsLoadError() from exc
```

`modules/trips/service.py`:

```python
from . import repository
from .schemas import TripRead


def get_trips(db: Session, account_id: uuid.UUID) -> list[TripRead]:
    return [TripRead.model_validate(t) for t in repository.list_for_account(db, account_id)]
```

`modules/trips/router.py`:

```python
@router.get("", operation_id="getTrips")
def get_trips(account: CurrentAccountDep, db: DbSessionDep) -> list[TripRead]:
    return service.get_trips(db, account.id)
```

Primary keys reach the repository already parsed — turning a client string
into a `UUID` is the service's job, because "not a UUID" is a *not found*, not
a storage failure. See `meetups/service.py:get_owned_meetup`.

`db: DbSessionDep` is the same session dependency `accounts` already
uses — never construct a new engine or `Session()` directly; always take
`db` as a parameter and pass it down, so one request uses one transaction
end-to-end. A scheduled job has no request to hang off, so it opens its own:
`with SessionLocal() as db:` (see `docs/architecture.md`).

`scrape-events` is the exception, and opens `ScraperSessionLocal()` instead. It
connects as a role that owns the `content` schema and has no grant on the
application's tables, so a bug in scraping cannot reach an account or a meetup —
see "Who connects as what" in `docs/architecture.md`. Anything that job touches
has to live in `content`; put a table it writes in `public` and it needs a grant
there, which undoes the arrangement.
