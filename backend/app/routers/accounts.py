from fastapi import APIRouter

from ..core.exceptions import (
    AccountUpsertError,
    InvalidBearerTokenError,
    MissingBearerTokenError,
    UnauthenticatedRoleError,
    default_responses,
    responses,
)
from ..schemas.account import AccountRead
from ..services.accounts import CurrentAccountDep

router = APIRouter(
    prefix="/api/accounts",
    tags=["accounts"],
    responses=default_responses(),
)


@router.get(
    "/me",
    operation_id="getMyAccount",
    responses=responses(
        MissingBearerTokenError,
        InvalidBearerTokenError,
        UnauthenticatedRoleError,
        AccountUpsertError,
    ),
)
def get_my_account(account: CurrentAccountDep) -> AccountRead:
    return AccountRead.model_validate(account)
