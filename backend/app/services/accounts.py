import logging
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..auth.supabase import CurrentIdentityDep, SupabaseIdentity
from ..core.exceptions import AccountUpsertError
from ..db.session import DbSessionDep
from ..models.account import Account

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
    but never touch display_name or avatar_url - see Account.profile_customized for why
    they're absent below.
    """
    display_name, avatar_url, providers, metadata = _extract_profile(identity.claims)

    insert_statement = insert(Account).values(
        supabase_user_id=identity.supabase_user_id,
        email=identity.email,
        display_name=display_name,
        avatar_url=avatar_url,
        profile_customized=False,
        providers=providers,
        auth_metadata=metadata,
        last_seen_at=func.now(),
    )
    statement = insert_statement.on_conflict_do_update(
        index_elements=[Account.supabase_user_id],
        set_={
            "email": insert_statement.excluded.email,
            "providers": insert_statement.excluded.providers,
            "auth_metadata": insert_statement.excluded.auth_metadata,
            "last_seen_at": func.now(),
            "updated_at": func.now(),
        },
    ).returning(Account)

    try:
        account = db.execute(statement).scalar_one()
        db.commit()
    except SQLAlchemyError as exc:
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("Failed to roll back after account upsert error")
        raise AccountUpsertError() from exc

    return account


def get_current_account(identity: CurrentIdentityDep, db: DbSessionDep) -> Account:
    return get_or_create_account(db, identity)


CurrentAccountDep = Annotated[Account, Depends(get_current_account)]
