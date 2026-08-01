# Auth & database patterns

How to get the current user, protect an endpoint, add a table, and query it —
using the conventions already established by `accounts`.

## 1. Getting the current user

`CurrentIdentityDep` (`app/auth/supabase.py`) gives you a `SupabaseIdentity` —
the **verified JWT identity**: `supabase_user_id`, `email`, raw `claims`. It
is not a database row; `get_current_identity` rebuilds it from scratch on
every request.

Almost every authenticated endpoint wants the actual `Account` row (`id`,
`display_name`, `providers`, etc.) rather than just the claims, so that's
wrapped as its own dependency, `CurrentAccountDep` (`app/services/accounts.py`):

```python
def get_current_account(identity: CurrentIdentityDep, db: DbSessionDep) -> Account:
    return get_or_create_account(db, identity)

CurrentAccountDep = Annotated[Account, Depends(get_current_account)]
```

It lives in `services/accounts.py`, not next to `CurrentIdentityDep` in
`auth/supabase.py` — `services/accounts.py` already imports `SupabaseIdentity`
from `auth/supabase.py`, so defining `CurrentAccountDep` inside
`auth/supabase.py` instead (importing `get_or_create_account` back from
`services/accounts.py`) would be a circular import. Any dependency that
composes a service function with an auth dependency belongs in the service
module; the same reasoning applies for a future `CurrentTripDep` or similar.

`routers/accounts.py` uses it directly — one dependency, no manual call to
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
at all — like `get_members` in `meetup.py`.

**Protected, one route** — add the dependency as a parameter, same as
`accounts.py`:

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

Same four files as `Account`, then two Alembic commands.

1. **Model** — `app/models/trip.py`, same shape as `account.py` (`Base`,
   `Mapped`/`mapped_column`; the naming convention is already global via
   `Base.metadata`).
2. **Register it** in `app/models/__init__.py` *and* `migrations/env.py` —
   this import is the easy-to-forget step. Alembic only compares tables it
   has actually imported into `Base.metadata`; a model that exists but was
   never imported there is invisible to autogenerate and won't be created.

   ```python
   # app/models/__init__.py
   from .account import Account
   from .trip import Trip

   __all__ = ["Account", "Trip"]
   ```

   ```python
   # migrations/env.py
   from app.models import Account, Trip  # noqa: F401
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
router  → thin HTTP glue: pulls deps, calls a service function, returns
service → the actual query/business logic, takes `db: Session` as a parameter
model   → the SQLAlchemy table
schema  → the Pydantic shape returned to the client
```

Routers never touch SQLAlchemy directly — they always go through a service
function, the same way `routers/accounts.py` never calls `get_or_create_account`
itself; it only ever goes through `CurrentAccountDep`, which calls it once
inside `services/accounts.py`.

`services/trips.py`:

```python
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.trip import Trip
from ..schemas.trip import CreateTripRequest


def list_trips_for_account(db: Session, account_id: uuid.UUID) -> list[Trip]:
    return db.scalars(select(Trip).where(Trip.account_id == account_id)).all()


def create_trip(db: Session, account_id: uuid.UUID, data: CreateTripRequest) -> Trip:
    trip = Trip(account_id=account_id, name=data.name)
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip
```

`routers/trips.py`:

```python
@router.get("/")
def list_trips(account: CurrentAccountDep, db: DbSessionDep) -> list[TripRead]:
    return [TripRead.model_validate(t) for t in trips.list_trips_for_account(db, account.id)]
```

`db: DbSessionDep` is the same session dependency `accounts.py` already
uses — never construct a new engine or `Session()` directly; always take
`db` as a parameter and pass it down, so one request uses one transaction
end-to-end.
