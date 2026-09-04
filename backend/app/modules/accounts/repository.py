"""Every SQLAlchemy statement the accounts module runs.

`SQLAlchemyError` never leaves this file.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import AccountUpsertError
from .models import Account

logger = logging.getLogger(__name__)


def upsert_account(
    db: Session,
    *,
    supabase_user_id: uuid.UUID,
    email: str | None,
    display_name: str | None,
    avatar_url: str | None,
    providers: list[str],
    auth_metadata: dict[str, Any],
) -> Account:
    """Insert the account, or refresh the volatile columns of the existing row.

    `display_name` and `avatar_url` are supplied for the insert but absent from
    the update on purpose - see `Account.profile_customized`.
    """
    insert_statement = insert(Account).values(
        supabase_user_id=supabase_user_id,
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
        profile_customized=False,
        providers=providers,
        auth_metadata=auth_metadata,
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
