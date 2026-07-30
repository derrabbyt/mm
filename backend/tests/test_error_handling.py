import pytest

from app.cache import people as members_cache
from app.core.exceptions import (
    AppBaseException,
    MemberCreateError,
    MemberLoadError,
    MemberNotFoundError,
    MembersLoadError,
    MemberUpdateError,
    responses,
)
from app.schemas.member import MeetupMember, UpdateMeetupMemberRequest
from app.schemas.position import Position
from app.services import members

STORAGE_ERRORS = [
    MemberLoadError,
    MembersLoadError,
    MemberUpdateError,
    MemberCreateError,
]


class BoomRedis:
    """Redis client where every call fails at the connection level."""

    def __getattr__(self, _name):
        async def _fail(*args, **kwargs):
            raise ConnectionError("redis is down")

        return _fail


def _update_request() -> UpdateMeetupMemberRequest:
    return UpdateMeetupMemberRequest(
        name="Ada", position=Position(latitude=48.2, longitude=16.4)
    )


# --- exception classes ------------------------------------------------------


def test_not_found_is_a_client_error():
    exc = MemberNotFoundError(member_id="42")
    assert exc.status == 404
    assert exc.code == "member_not_found"
    assert exc.message == "Member 42 not found"


@pytest.mark.parametrize("error", STORAGE_ERRORS)
def test_storage_errors_are_503(error):
    """A Redis outage is our fault, and retrying the same request may succeed."""
    assert error.status == 503
    assert issubclass(error, AppBaseException)


def test_error_codes_are_unique():
    codes = [e.code for e in [MemberNotFoundError, *STORAGE_ERRORS]]
    assert len(codes) == len(set(codes))


# --- responses() ------------------------------------------------------------


def test_responses_keys_by_status():
    assert set(responses(MemberNotFoundError, MemberLoadError)) == {404, 503}


def test_responses_discriminates_same_status_errors_on_error():
    """Same-status errors become a union tagged by `error`, each keeping its schema."""
    from pydantic import TypeAdapter

    adapter = TypeAdapter(responses(MemberLoadError, MembersLoadError)[503]["model"])

    loaded = adapter.validate_python({"error": "member_load_error", "message": "x"})
    assert type(loaded).__name__ == "MemberLoadError"

    listed = adapter.validate_python({"error": "members_load_error", "message": "x"})
    assert type(listed).__name__ == "MembersLoadError"

    with pytest.raises(ValueError):
        adapter.validate_python({"error": "member_not_found", "message": "x"})


def test_responses_uses_the_bare_model_for_a_lone_error():
    model = responses(MemberNotFoundError)[404]["model"]
    assert model.__name__ == "MemberNotFoundError"

    model(error="member_not_found", message="x")
    with pytest.raises(ValueError):
        model(error="something_else", message="x")


def test_error_schema_names_are_unique():
    """Two exceptions sharing a __name__ would silently collide in the schema."""
    errors = [MemberNotFoundError, *STORAGE_ERRORS]
    names = [e.model().__name__ for e in errors]
    assert len(names) == len(set(names))


# --- service layer ----------------------------------------------------------


async def test_get_member_raises_not_found_for_missing_id(redis):
    with pytest.raises(MemberNotFoundError):
        await members.get_member(redis, "404")


async def test_update_member_raises_not_found_for_missing_id(redis):
    with pytest.raises(MemberNotFoundError):
        await members.update_member(redis, "404", _update_request())


# --- cache layer translates infrastructure failures -------------------------


async def test_get_translates_redis_failure():
    with pytest.raises(MemberLoadError) as info:
        await members_cache.get(BoomRedis(), "1")
    assert isinstance(info.value.__cause__, ConnectionError)


async def test_list_all_translates_redis_failure():
    with pytest.raises(MembersLoadError):
        await members_cache.list_all(BoomRedis())


async def test_next_id_translates_redis_failure():
    """Previously unguarded, which surfaced as a bare 500 on POST."""
    with pytest.raises(MemberCreateError):
        await members_cache.next_id(BoomRedis())


class _BoomPipeline:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def set(self, *a, **kw):
        return self

    def sadd(self, *a, **kw):
        return self

    async def execute(self):
        raise ConnectionError("redis is down")


class _RedisWithBoomPipeline:
    def pipeline(self, *a, **kw):
        return _BoomPipeline()


async def test_put_translates_redis_failure():
    with pytest.raises(MemberUpdateError):
        await members_cache.put(
            _RedisWithBoomPipeline(), MeetupMember(id="1", name="Ada")
        )


# --- HTTP surface -----------------------------------------------------------


async def test_missing_member_returns_404(client):
    resp = await client.get("/api/meetup/members/404")
    assert resp.status_code == 404
    assert resp.json() == {
        "error": "member_not_found",
        "message": "Member 404 not found",
    }


async def test_storage_outage_returns_503(broken_client):
    resp = await broken_client.get("/api/meetup/members")
    assert resp.status_code == 503
    assert resp.json()["error"] == "members_load_error"


async def test_get_member_during_outage_returns_503(broken_client):
    resp = await broken_client.get("/api/meetup/members/1")
    assert resp.status_code == 503
    assert resp.json()["error"] == "member_load_error"


async def test_post_during_outage_returns_503(broken_client):
    resp = await broken_client.post("/api/meetup/members", json={"name": "Ada"})
    assert resp.status_code == 503
    assert resp.json()["error"] == "member_create_error"


async def test_unhandled_exception_returns_generic_500(client, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(members_cache, "list_all", boom)

    resp = await client.get("/api/meetup/members")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "internal_server_error"
    assert "secret internal detail" not in body["message"]


async def test_unknown_route_uses_the_envelope(client):
    resp = await client.get("/api/meetup/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "http_404"


async def test_bad_method_uses_the_envelope(client):
    resp = await client.request("DELETE", "/api/meetup/members")
    assert resp.status_code == 405
    assert resp.json()["error"] == "http_405"


async def test_validation_error_uses_the_envelope(client):
    resp = await client.post("/api/meetup/members", json={})
    assert resp.status_code == 422

    body = resp.json()
    assert body["error"] == "validation_error"
    assert "detail" not in body  # FastAPI's own shape must not leak through
    assert body["details"] == [
        {"location": ["body", "name"], "message": "Field required", "type": "missing"}
    ]


async def test_validation_error_reports_every_bad_field(client):
    """Reshaping into our envelope must not lose which fields failed."""
    resp = await client.put(
        "/api/meetup/members/1",
        json={"position": {"latitude": "not-a-number"}},
    )
    assert resp.status_code == 422

    locations = {tuple(d["location"]) for d in resp.json()["details"]}
    assert ("body", "name") in locations
    assert ("body", "position", "latitude") in locations
    assert ("body", "position", "longitude") in locations


async def test_details_are_absent_on_other_errors(client):
    assert "details" not in (await client.get("/api/meetup/members/404")).json()
    assert "details" not in (await client.get("/api/meetup/nope")).json()


async def test_validation_error_does_not_echo_the_body(client):
    resp = await client.post(
        "/api/meetup/members", json={"name": 123, "secret": "hunter2"}
    )
    assert resp.status_code == 422
    assert "hunter2" not in resp.text


async def test_happy_path_still_works(client):
    created = await client.post("/api/meetup/members", json={"name": "Ada"})
    assert created.status_code == 200

    fetched = await client.get(f"/api/meetup/members/{created.json()['id']}")
    assert fetched.json()["name"] == "Ada"


# --- OpenAPI ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "method", "status", "schema_name"),
    [
        ("/api/meetup/members", "get", "503", "MembersLoadError"),
        ("/api/meetup/members", "post", "503", "MemberCreateError"),
        ("/api/meetup/members/{member_id}", "get", "404", "MemberNotFoundError"),
        ("/api/meetup/members/{member_id}", "get", "503", "MemberLoadError"),
        ("/api/meetup/members/{member_id}", "put", "404", "MemberNotFoundError"),
    ],
)
async def test_openapi_documents_each_error(client, path, method, status, schema_name):
    schema = (await client.get("/api/openapi.json")).json()
    responses_ = schema["paths"][path][method]["responses"]

    ref = responses_[status]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith(f"/{schema_name}")


async def test_openapi_uses_a_discriminated_oneof_for_shared_statuses(client):
    """PUT raises two different 503s.

    oneOf + discriminator is what makes the generator emit a real TS union; a
    bare anyOf silently collapses to its first branch and loses the other codes.
    """
    schema = (await client.get("/api/openapi.json")).json()
    responses_ = schema["paths"]["/api/meetup/members/{member_id}"]["put"]["responses"]
    body = responses_["503"]["content"]["application/json"]["schema"]

    refs = {option["$ref"].rsplit("/", 1)[-1] for option in body["oneOf"]}
    assert refs == {"MemberLoadError", "MemberUpdateError"}

    discriminator = body["discriminator"]
    assert discriminator["propertyName"] == "error"
    assert set(discriminator["mapping"]) == {
        "member_load_error",
        "member_update_error",
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
    model = schema["components"]["schemas"]["MemberNotFoundError"]

    assert model["properties"]["error"]["const"] == "member_not_found"


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
