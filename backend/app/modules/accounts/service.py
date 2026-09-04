import logging
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.orm import Session

from ...db.session import DbSessionDep
from . import repository
from .identity import CurrentIdentityDep, SupabaseIdentity
from .models import Account

logger = logging.getLogger(__name__)

_METADATA_KEYS = {"full_name", "name", "user_name", "preferred_username", "locale"}


def _extract_profile(
    claims: dict[str, Any],
) -> tuple[str | None, str | None, list[str], dict[str, Any]]:
    user_metadata = claims.get("user_metadata") or {}
    app_metadata = claims.get("app_metadata") or {}

    display_name = (
        user_metadata.get("full_name")
        or user_metadata.get("name")
        or user_metadata.get("user_name")
        or user_metadata.get("preferred_username")
    )
    avatar_url = user_metadata.get("avatar_url") or user_metadata.get("picture")

    providers = app_metadata.get("providers") or []
    if not providers and app_metadata.get("provider"):
        providers = [app_metadata["provider"]]

    metadata = {k: v for k, v in user_metadata.items() if k in _METADATA_KEYS}

    return display_name, avatar_url, providers, metadata


def get_or_create_account(db: Session, identity: SupabaseIdentity) -> Account:
    """First login creates the account. Later logins refresh email/providers/last_seen_at
    but never touch display_name or avatar_url - see Account.profile_customized for why.
    """
    display_name, avatar_url, providers, metadata = _extract_profile(identity.claims)

    return repository.upsert_account(
        db,
        supabase_user_id=identity.supabase_user_id,
        email=identity.email,
        display_name=display_name,
        avatar_url=avatar_url,
        providers=providers,
        auth_metadata=metadata,
    )


def get_current_account(identity: CurrentIdentityDep, db: DbSessionDep) -> Account:
    return get_or_create_account(db, identity)


CurrentAccountDep = Annotated[Account, Depends(get_current_account)]
