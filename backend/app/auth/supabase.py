import uuid
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel

from ..core.config import settings
from ..core.exceptions import (
    InvalidBearerTokenError,
    MissingBearerTokenError,
    UnauthenticatedRoleError,
)

bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None, Security(bearer_scheme)
]


class SupabaseIdentity(BaseModel):
    """A verified JWT's claims - proof of who's asking, not a database row.
    Rebuilt from scratch on every request; see Account for the persisted row."""

    supabase_user_id: uuid.UUID
    email: str | None
    claims: dict[str, Any]


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    return PyJWKClient(settings.supabase_jwks_url, cache_keys=True)


def get_current_identity(credentials: BearerCredentials) -> SupabaseIdentity:
    if credentials is None:
        raise MissingBearerTokenError()

    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(
            credentials.credentials
        )
        claims: dict[str, Any] = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            issuer=settings.supabase_issuer,
            audience=settings.supabase_jwt_audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp"]},
        )
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise InvalidBearerTokenError() from exc

    if claims.get("role") != "authenticated":
        raise UnauthenticatedRoleError()

    return SupabaseIdentity(
        supabase_user_id=user_id,
        email=claims.get("email"),
        claims=claims,
    )


CurrentIdentityDep = Annotated[SupabaseIdentity, Depends(get_current_identity)]
