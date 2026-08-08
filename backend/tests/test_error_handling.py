import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import OperationalError

from app.core.exceptions import (
    AccountNotFoundError,
    AppBaseException,
    MeetupCreateError,
    MeetupLoadError,
    MeetupNotFoundError,
    MeetupsLoadError,
    ParticipantCreateError,
    ParticipantLoadError,
    ParticipantNotFoundError,
    ParticipantsLoadError,
    ParticipantUpdateError,
    responses,
)
from app.models.account import Account
from app.models.meetup_participant import MeetupParticipant
from app.schemas.meetup import CreateMeetupRequest
from app.schemas.participant import AddParticipantRequest, UpdateParticipantRequest
from app.schemas.position import Position
from app.services import meetups, participants

STORAGE_ERRORS = [
    MeetupLoadError,
    MeetupsLoadError,
    MeetupCreateError,
    ParticipantLoadError,
    ParticipantsLoadError,
    ParticipantUpdateError,
    ParticipantCreateError,
]

NOT_FOUND_ERRORS = [MeetupNotFoundError, ParticipantNotFoundError, AccountNotFoundError]


def _boom() -> OperationalError:
    return OperationalError("<test>", {}, Exception("database is down"))


class BoomSession:
    """A DB session where every operation fails at the connection level."""

    def __getattr__(self, _name):
        def _fail(*args, **kwargs):
            raise _boom()

        return _fail


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class MeetupFoundThenBoomSession:
    """Resolves the meetup fine, then fails on the participant work.

    Mirrors losing the connection partway through a request: without this the
    session would already blow up on the ownership check and every participant
    failure would surface as a meetup error instead of its own.
    """

    def __init__(self, meetup, existing: MeetupParticipant | None = None):
        self._meetup = meetup
        self._existing = existing

    def get(self, *args, **kwargs):
        return self._meetup

    def scalars(self, *args, **kwargs):
        if self._existing is None:
            raise _boom()
        return _Scalars([self._existing])

    def add(self, *args, **kwargs):
        pass

    def commit(self):
        raise _boom()

    def rollback(self):
        pass

    def refresh(self, *args, **kwargs):
        pass


def _update_request() -> UpdateParticipantRequest:
    return UpdateParticipantRequest(
        name="Ada",
        travel_mode="transit",
        position=Position(latitude=48.2, longitude=16.4),
    )


def _meetup_payload() -> dict:
    return {
        "name": "Coffee",
        "location": "Vienna",
        "starts_at": datetime.now(UTC).isoformat(),
    }


# --- exception classes ------------------------------------------------------


def test_not_found_is_a_client_error():
    exc = ParticipantNotFoundError(participant_id="42")
    assert exc.status == 404
    assert exc.code == "participant_not_found"
    assert exc.message == "Participant 42 not found"


def test_missing_meetup_is_a_client_error():
    exc = MeetupNotFoundError(meetup_id="42")
    assert exc.status == 404
    assert exc.code == "meetup_not_found"
    assert exc.message == "Meetup 42 not found"


@pytest.mark.parametrize("error", STORAGE_ERRORS)
def test_storage_errors_are_503(error):
    """A storage outage is our fault, and retrying the same request may succeed."""
    assert error.status == 503
    assert issubclass(error, AppBaseException)


def test_error_codes_are_unique():
    codes = [e.code for e in [*NOT_FOUND_ERRORS, *STORAGE_ERRORS]]
    assert len(codes) == len(set(codes))


# --- responses() ------------------------------------------------------------


def test_responses_keys_by_status():
    assert set(responses(ParticipantNotFoundError, ParticipantLoadError)) == {404, 503}


def test_responses_discriminates_same_status_errors_on_error():
    """Same-status errors become a union tagged by `error`, each keeping its schema."""
    from pydantic import TypeAdapter

    adapter = TypeAdapter(
        responses(ParticipantLoadError, ParticipantsLoadError)[503]["model"]
    )

    loaded = adapter.validate_python(
        {"error": "participant_load_error", "message": "x"}
    )
    assert type(loaded).__name__ == "ParticipantLoadError"

    listed = adapter.validate_python(
        {"error": "participants_load_error", "message": "x"}
    )
    assert type(listed).__name__ == "ParticipantsLoadError"

    with pytest.raises(ValueError):
        adapter.validate_python({"error": "participant_not_found", "message": "x"})


def test_responses_uses_the_bare_model_for_a_lone_error():
    model = responses(ParticipantNotFoundError)[404]["model"]
    assert model.__name__ == "ParticipantNotFoundError"

    model(error="participant_not_found", message="x")
    with pytest.raises(ValueError):
        model(error="something_else", message="x")


def test_error_schema_names_are_unique():
    """Two exceptions sharing a __name__ would silently collide in the schema."""
    errors = [*NOT_FOUND_ERRORS, *STORAGE_ERRORS]
    names = [e.model().__name__ for e in errors]
    assert len(names) == len(set(names))


# --- service layer ----------------------------------------------------------


def test_get_participant_raises_not_found_for_missing_id(db, meetup, account):
    with pytest.raises(ParticipantNotFoundError):
        participants.get_participant(db, str(meetup.id), str(uuid.uuid4()), account.id)


def test_update_participant_raises_not_found_for_missing_id(db, meetup, account):
    with pytest.raises(ParticipantNotFoundError):
        participants.update_participant(
            db, str(meetup.id), str(uuid.uuid4()), account.id, _update_request()
        )


def test_get_participant_rejects_a_malformed_id_as_not_found(db, meetup, account):
    """A non-UUID id is just as absent as an unknown one - same 404, not a 500."""
    with pytest.raises(ParticipantNotFoundError):
        participants.get_participant(db, str(meetup.id), "not-a-uuid", account.id)


def test_missing_meetup_reads_as_not_found(db, account):
    with pytest.raises(MeetupNotFoundError):
        participants.get_participants(db, str(uuid.uuid4()), account.id)


def test_malformed_meetup_id_reads_as_not_found(db, account):
    with pytest.raises(MeetupNotFoundError):
        participants.get_participants(db, "not-a-uuid", account.id)


# --- ownership --------------------------------------------------------------


def test_another_accounts_meetup_is_not_found(db, meetup):
    """Not 403: an unrelated account must not learn that the id exists."""
    stranger_id = uuid.uuid4()
    with pytest.raises(MeetupNotFoundError):
        meetups.get_meetup(db, str(meetup.id), stranger_id)


def test_participants_of_another_accounts_meetup_are_not_found(db, meetup, account):
    participants.add_participant(
        db, str(meetup.id), account.id, AddParticipantRequest(name="Ada")
    )

    with pytest.raises(MeetupNotFoundError):
        participants.get_participants(db, str(meetup.id), uuid.uuid4())


def test_get_meetups_only_returns_your_own(db, meetup, account):
    assert [m.id for m in meetups.get_meetups(db, account.id)] == [meetup.id]
    assert meetups.get_meetups(db, uuid.uuid4()) == []


# --- service layer translates infrastructure failures -----------------------


def test_get_meetups_translates_db_failure():
    with pytest.raises(MeetupsLoadError):
        meetups.get_meetups(BoomSession(), uuid.uuid4())


def test_create_meetup_translates_db_failure():
    with pytest.raises(MeetupCreateError):
        meetups.create_meetup(
            BoomSession(),
            uuid.uuid4(),
            CreateMeetupRequest(
                name="Coffee", location="Vienna", starts_at=datetime.now(UTC)
            ),
        )


def test_get_meetup_translates_db_failure():
    with pytest.raises(MeetupLoadError) as info:
        meetups.get_meetup(BoomSession(), str(uuid.uuid4()), uuid.uuid4())
    assert isinstance(info.value.__cause__, OperationalError)


def test_get_participants_translates_db_failure(meetup, account):
    with pytest.raises(ParticipantsLoadError):
        participants.get_participants(
            MeetupFoundThenBoomSession(meetup), str(meetup.id), account.id
        )


def test_get_participant_translates_db_failure(meetup, account):
    with pytest.raises(ParticipantLoadError) as info:
        participants.get_participant(
            MeetupFoundThenBoomSession(meetup),
            str(meetup.id),
            str(uuid.uuid4()),
            account.id,
        )
    assert isinstance(info.value.__cause__, OperationalError)


def test_add_participant_translates_db_failure(meetup, account):
    with pytest.raises(ParticipantCreateError):
        participants.add_participant(
            MeetupFoundThenBoomSession(meetup),
            str(meetup.id),
            account.id,
            AddParticipantRequest(name="Ada"),
        )


def test_update_participant_translates_db_failure(meetup, account):
    existing = MeetupParticipant(
        id=uuid.uuid4(), meetup_id=meetup.id, name="Ada", travel_mode="transit"
    )
    with pytest.raises(ParticipantUpdateError):
        participants.update_participant(
            MeetupFoundThenBoomSession(meetup, existing),
            str(meetup.id),
            str(existing.id),
            account.id,
            _update_request(),
        )


# --- the linked account is changeable ---------------------------------------


def _second_account(db) -> uuid.UUID:
    other = Account(
        id=uuid.uuid4(), supabase_user_id=uuid.uuid4(), display_name="Grace"
    )
    db.add(other)
    db.commit()
    return other.id


def test_participant_can_be_linked_to_an_account_after_creation(db, meetup, account):
    added = participants.add_participant(
        db, str(meetup.id), account.id, AddParticipantRequest(name="Ada")
    )
    assert added.account_id is None

    linked = participants.update_participant(
        db,
        str(meetup.id),
        str(added.id),
        account.id,
        UpdateParticipantRequest(
            name="Ada",
            position=Position(latitude=48.2, longitude=16.4),
            account_id=account.id,
        ),
    )
    assert linked.account_id == account.id


def test_a_linked_account_can_be_swapped(db, meetup, account):
    other_id = _second_account(db)
    added = participants.add_participant(
        db,
        str(meetup.id),
        account.id,
        AddParticipantRequest(name="Ada", account_id=account.id),
    )
    assert added.account_id == account.id

    relinked = participants.update_participant(
        db,
        str(meetup.id),
        str(added.id),
        account.id,
        UpdateParticipantRequest(
            name="Ada",
            position=Position(latitude=48.2, longitude=16.4),
            account_id=other_id,
        ),
    )
    assert relinked.account_id == other_id


def test_omitting_the_account_unlinks_it(db, meetup, account):
    """PUT is a full replace, so a caller that drops account_id clears it."""
    added = participants.add_participant(
        db,
        str(meetup.id),
        account.id,
        AddParticipantRequest(name="Ada", account_id=account.id),
    )

    unlinked = participants.update_participant(
        db, str(meetup.id), str(added.id), account.id, _update_request()
    )
    assert unlinked.account_id is None


def test_linking_an_unknown_account_on_update_is_a_404(db, meetup, account):
    """A dangling foreign key is the caller's mistake, not a storage outage."""
    added = participants.add_participant(
        db, str(meetup.id), account.id, AddParticipantRequest(name="Ada")
    )

    with pytest.raises(AccountNotFoundError):
        participants.update_participant(
            db,
            str(meetup.id),
            str(added.id),
            account.id,
            UpdateParticipantRequest(
                name="Ada",
                position=Position(latitude=48.2, longitude=16.4),
                account_id=uuid.uuid4(),
            ),
        )


def test_linking_an_unknown_account_on_add_is_a_404(db, meetup, account):
    with pytest.raises(AccountNotFoundError):
        participants.add_participant(
            db,
            str(meetup.id),
            account.id,
            AddParticipantRequest(name="Ada", account_id=uuid.uuid4()),
        )


async def test_relinking_round_trips_over_http(client, meetup, db, account):
    other_id = _second_account(db)
    created = await client.post(
        f"/api/meetups/{meetup.id}/participants", json={"name": "Ada"}
    )
    participant_id = created.json()["id"]

    relinked = await client.put(
        f"/api/meetups/{meetup.id}/participants/{participant_id}",
        json={
            "name": "Ada",
            "travel_mode": "transit",
            "position": {"latitude": 48.2, "longitude": 16.4},
            "account_id": str(other_id),
        },
    )
    assert relinked.status_code == 200
    assert relinked.json()["account_id"] == str(other_id)


async def test_an_unknown_travel_mode_is_rejected(client, meetup):
    resp = await client.post(
        f"/api/meetups/{meetup.id}/participants",
        json={"name": "Ada", "travel_mode": "teleport"},
    )
    assert resp.status_code == 422
    assert ("body", "travel_mode") in {
        tuple(d["location"]) for d in resp.json()["details"]
    }


async def test_unknown_account_over_http_is_a_404(client, meetup):
    created = await client.post(
        f"/api/meetups/{meetup.id}/participants", json={"name": "Ada"}
    )
    unknown = uuid.uuid4()

    resp = await client.put(
        f"/api/meetups/{meetup.id}/participants/{created.json()['id']}",
        json={
            "name": "Ada",
            "position": {"latitude": 48.2, "longitude": 16.4},
            "account_id": str(unknown),
        },
    )
    assert resp.status_code == 404
    assert resp.json() == {
        "error": "account_not_found",
        "message": f"Account {unknown} not found",
    }


def test_an_unplaced_participant_can_be_renamed(db, meetup, account):
    added = participants.add_participant(
        db, str(meetup.id), account.id, AddParticipantRequest(name="Ada")
    )

    renamed = participants.update_participant(
        db,
        str(meetup.id),
        str(added.id),
        account.id,
        UpdateParticipantRequest(name="Grace", travel_mode="walk"),
    )
    assert renamed.name == "Grace"
    assert renamed.travel_mode == "walk"
    assert renamed.position is None


def test_omitting_the_position_clears_it(db, meetup, account):
    added = participants.add_participant(
        db, str(meetup.id), account.id, AddParticipantRequest(name="Ada")
    )
    participants.update_participant(
        db, str(meetup.id), str(added.id), account.id, _update_request()
    )

    cleared = participants.update_participant(
        db,
        str(meetup.id),
        str(added.id),
        account.id,
        UpdateParticipantRequest(name="Ada"),
    )
    assert cleared.position is None


# --- service layer round trip -----------------------------------------------


def test_participant_starts_unplaced_and_can_be_placed(db, meetup, account):
    added = participants.add_participant(
        db, str(meetup.id), account.id, AddParticipantRequest(name="Ada")
    )
    assert added.position is None
    assert added.travel_mode == "transit"

    placed = participants.update_participant(
        db, str(meetup.id), str(added.id), account.id, _update_request()
    )
    assert placed.position == Position(latitude=48.2, longitude=16.4)


# --- HTTP surface -----------------------------------------------------------


async def test_missing_meetup_returns_404(client):
    resp = await client.get("/api/meetups/404")
    assert resp.status_code == 404
    assert resp.json() == {
        "error": "meetup_not_found",
        "message": "Meetup 404 not found",
    }


async def test_missing_participant_returns_404(client, meetup):
    resp = await client.get(f"/api/meetups/{meetup.id}/participants/404")
    assert resp.status_code == 404
    assert resp.json() == {
        "error": "participant_not_found",
        "message": "Participant 404 not found",
    }


async def test_storage_outage_returns_503(broken_client):
    resp = await broken_client.get("/api/meetups")
    assert resp.status_code == 503
    assert resp.json()["error"] == "meetups_load_error"


async def test_get_meetup_during_outage_returns_503(broken_client):
    resp = await broken_client.get(f"/api/meetups/{uuid.uuid4()}")
    assert resp.status_code == 503
    assert resp.json()["error"] == "meetup_load_error"


async def test_post_during_outage_returns_503(broken_client):
    resp = await broken_client.post("/api/meetups", json=_meetup_payload())
    assert resp.status_code == 503
    assert resp.json()["error"] == "meetup_create_error"


async def test_unhandled_exception_returns_generic_500(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(meetups, "get_meetups", boom)

    resp = await client.get("/api/meetups")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "internal_server_error"
    assert "secret internal detail" not in body["message"]


async def test_unknown_route_uses_the_envelope(client):
    resp = await client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "http_404"


async def test_bad_method_uses_the_envelope(client):
    resp = await client.request("DELETE", "/api/meetups")
    assert resp.status_code == 405
    assert resp.json()["error"] == "http_405"


async def test_validation_error_uses_the_envelope(client):
    resp = await client.post("/api/meetups", json={})
    assert resp.status_code == 422

    body = resp.json()
    assert body["error"] == "validation_error"
    assert "detail" not in body  # FastAPI's own shape must not leak through
    locations = {tuple(d["location"]) for d in body["details"]}
    assert ("body", "name") in locations
    assert ("body", "location") in locations
    assert ("body", "starts_at") in locations


async def test_validation_error_reports_every_bad_field(client, meetup):
    """Reshaping into our envelope must not lose which fields failed."""
    resp = await client.put(
        f"/api/meetups/{meetup.id}/participants/{uuid.uuid4()}",
        json={"position": {"latitude": "not-a-number"}},
    )
    assert resp.status_code == 422

    locations = {tuple(d["location"]) for d in resp.json()["details"]}
    assert ("body", "name") in locations
    assert ("body", "position", "latitude") in locations
    assert ("body", "position", "longitude") in locations


async def test_details_are_absent_on_other_errors(client):
    assert "details" not in (await client.get("/api/meetups/404")).json()
    assert "details" not in (await client.get("/api/nope")).json()


async def test_validation_error_does_not_echo_the_body(client):
    resp = await client.post("/api/meetups", json={"name": 123, "secret": "hunter2"})
    assert resp.status_code == 422
    assert "hunter2" not in resp.text


async def test_happy_path_still_works(client):
    meetup = await client.post("/api/meetups", json=_meetup_payload())
    assert meetup.status_code == 200
    meetup_id = meetup.json()["id"]

    created = await client.post(
        f"/api/meetups/{meetup_id}/participants", json={"name": "Ada"}
    )
    assert created.status_code == 200
    assert created.json()["position"] is None

    fetched = await client.get(
        f"/api/meetups/{meetup_id}/participants/{created.json()['id']}"
    )
    assert fetched.json()["name"] == "Ada"

    listed = await client.get(f"/api/meetups/{meetup_id}/participants")
    assert [p["name"] for p in listed.json()] == ["Ada"]


async def test_placing_a_participant_round_trips_over_http(client, meetup):
    created = await client.post(
        f"/api/meetups/{meetup.id}/participants", json={"name": "Ada"}
    )
    participant_id = created.json()["id"]

    placed = await client.put(
        f"/api/meetups/{meetup.id}/participants/{participant_id}",
        json={
            "name": "Ada",
            "travel_mode": "walk",
            "position": {"latitude": 48.2, "longitude": 16.4},
        },
    )
    assert placed.status_code == 200
    assert placed.json()["position"] == {"latitude": 48.2, "longitude": 16.4}
    assert placed.json()["travel_mode"] == "walk"


# --- OpenAPI ----------------------------------------------------------------

PARTICIPANTS = "/api/meetups/{meetup_id}/participants"
PARTICIPANT = "/api/meetups/{meetup_id}/participants/{participant_id}"


def _error_schemas(schema, path, method, status) -> set[str]:
    """Schema names documented for one response, whether it is a lone $ref or
    a discriminated union of several errors sharing the status."""
    body = schema["paths"][path][method]["responses"][status]["content"][
        "application/json"
    ]["schema"]
    if "$ref" in body:
        return {body["$ref"].rsplit("/", 1)[-1]}
    return {option["$ref"].rsplit("/", 1)[-1] for option in body["oneOf"]}


@pytest.mark.parametrize(
    ("path", "method", "status", "schema_name"),
    [
        ("/api/meetups", "get", "503", "MeetupsLoadError"),
        ("/api/meetups", "post", "503", "MeetupCreateError"),
        ("/api/meetups", "get", "401", "MissingBearerTokenError"),
        ("/api/meetups", "get", "403", "UnauthenticatedRoleError"),
        ("/api/meetups/{meetup_id}", "get", "404", "MeetupNotFoundError"),
        ("/api/meetups/{meetup_id}", "get", "503", "MeetupLoadError"),
        (PARTICIPANTS, "get", "503", "ParticipantsLoadError"),
        (PARTICIPANTS, "post", "503", "ParticipantCreateError"),
        (PARTICIPANTS, "get", "404", "MeetupNotFoundError"),
        (PARTICIPANT, "put", "404", "ParticipantNotFoundError"),
        (PARTICIPANT, "put", "404", "AccountNotFoundError"),
        (PARTICIPANTS, "post", "404", "AccountNotFoundError"),
        (PARTICIPANT, "put", "503", "ParticipantUpdateError"),
    ],
)
async def test_openapi_documents_each_error(client, path, method, status, schema_name):
    schema = (await client.get("/api/openapi.json")).json()
    assert schema_name in _error_schemas(schema, path, method, status)


async def test_openapi_uses_a_discriminated_oneof_for_shared_statuses(client):
    """PUT raises several different 503s.

    oneOf + discriminator is what makes the generator emit a real TS union; a
    bare anyOf silently collapses to its first branch and loses the other codes.
    """
    schema = (await client.get("/api/openapi.json")).json()
    body = schema["paths"][PARTICIPANT]["put"]["responses"]["503"]["content"][
        "application/json"
    ]["schema"]

    refs = {option["$ref"].rsplit("/", 1)[-1] for option in body["oneOf"]}
    assert refs == {
        "AccountUpsertError",
        "MeetupLoadError",
        "ParticipantLoadError",
        "ParticipantUpdateError",
    }

    discriminator = body["discriminator"]
    assert discriminator["propertyName"] == "error"
    assert set(discriminator["mapping"]) == {
        "account_upsert_error",
        "meetup_load_error",
        "participant_load_error",
        "participant_update_error",
    }


async def test_no_error_response_uses_an_untagged_union(client):
    """An untagged anyOf collapses to one branch in the generated client.

    Every multi-variant error response must be a discriminated oneOf instead.
    """
    schema = (await client.get("/api/openapi.json")).json()

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for status, response in operation.get("responses", {}).items():
                if status.startswith("2"):
                    continue
                body = response.get("content", {}).get("application/json", {})
                where = f"{method.upper()} {path} {status}"
                assert "anyOf" not in body.get("schema", {}), where
                if "oneOf" in body.get("schema", {}):
                    assert "discriminator" in body["schema"], where


async def test_openapi_pins_error_code_as_a_const(client):
    """The const is what makes the generated client a string enum."""
    schema = (await client.get("/api/openapi.json")).json()
    model = schema["components"]["schemas"]["ParticipantNotFoundError"]

    assert model["properties"]["error"]["const"] == "participant_not_found"


async def test_every_route_documents_the_envelope_for_422(client):
    """The 422 handler is overridden, so no route may still promise FastAPI's shape.

    Set once at router level via default_responses(); this catches a new router
    that forgets it.
    """
    schema = (await client.get("/api/openapi.json")).json()

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            response = operation.get("responses", {}).get("422")
            assert response is not None, f"{method.upper()} {path} documents no 422"

            ref = response["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ValidationError"), f"{method.upper()} {path} -> {ref}"


async def test_every_participant_route_requires_a_bearer_token(client):
    """The generated client only sends the token where security is declared."""
    schema = (await client.get("/api/openapi.json")).json()

    for path in ("/api/meetups", "/api/meetups/{meetup_id}", PARTICIPANTS, PARTICIPANT):
        for method, operation in schema["paths"][path].items():
            names = {name for scheme in operation["security"] for name in scheme}
            assert "HTTPBearer" in names, f"{method.upper()} {path}"


async def test_fastapis_validation_schema_is_gone(client):
    schema = (await client.get("/api/openapi.json")).json()
    assert "HTTPValidationError" not in schema["components"]["schemas"]


async def test_openapi_has_no_dangling_refs(client):
    schema = (await client.get("/api/openapi.json")).json()
    defined = set(schema["components"]["schemas"])
    referenced = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                referenced.add(ref.rsplit("/", 1)[-1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    assert referenced <= defined
