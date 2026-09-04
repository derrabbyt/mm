from fastapi import APIRouter

from ...core.exceptions import AUTH_ERRORS, default_responses, responses
from .schemas import AccountRead
from .service import CurrentAccountDep

router = APIRouter(
    prefix="/api/accounts",
    tags=["accounts"],
    responses=default_responses(),
)


@router.get(
    "/me",
    operation_id="getMyAccount",
    responses=responses(*AUTH_ERRORS),
)
def get_my_account(account: CurrentAccountDep) -> AccountRead:
    return AccountRead.model_validate(account)
