from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import BaseModel, Field, create_model


class AppBaseException(Exception):
    code: ClassVar[str]
    status: ClassVar[int]

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

    @classmethod
    def model(cls) -> type[BaseModel]:
        """Response schema for this error, with `error` as the discriminator."""
        return create_model(
            cls.__name__,
            error=(Literal[cls.code], ...),
            message=(str, ...),
        )


class FieldError(BaseModel):
    location: list[str] = Field(
        description="Path to the field, e.g. ['body', 'name'].",
        examples=[["body", "name"]],
    )
    message: str = Field(
        description="What is wrong with it.",
        examples=["Field required"],
    )
    type: str = Field(
        description="Machine-readable failure kind.",
        examples=["missing"],
    )


class ValidationError(AppBaseException):
    """Raised by FastAPI"""

    code = "validation_error"
    status = 422

    def __init__(self, details: list[FieldError] | None = None):
        self.details = details or []
        super().__init__("The request payload failed validation.")

    @classmethod
    def model(cls) -> type[BaseModel]:
        return create_model(
            cls.__name__,
            error=(Literal[cls.code], ...),
            message=(str, ...),
            details=(list[FieldError], ...),
        )


class RouteNotFoundError(AppBaseException):
    """Raised by Starlette"""

    code = "http_404"
    status = 404


class MethodNotAllowedError(AppBaseException):
    """Raised by Starlette."""

    code = "http_405"
    status = 405


class InternalServerError(AppBaseException):
    code = "internal_server_error"
    status = 500

    def __init__(self):
        super().__init__("Something went wrong. Please try again later.")


class MemberNotFoundError(AppBaseException):
    code = "member_not_found"
    status = 404

    def __init__(self, member_id: str):
        super().__init__(f"Member {member_id} not found")


class MemberLoadError(AppBaseException):
    code = "member_load_error"
    status = 503

    def __init__(self, member_id: str):
        super().__init__(f"Member {member_id} could not be loaded")


class MembersLoadError(AppBaseException):
    code = "members_load_error"
    status = 503

    def __init__(self):
        super().__init__("Members could not be loaded")


class MemberUpdateError(AppBaseException):
    code = "member_update_error"
    status = 503

    def __init__(self, member_id: str):
        super().__init__(f"Member {member_id} could not be updated")


class MemberCreateError(AppBaseException):
    code = "member_create_error"
    status = 503

    def __init__(self):
        super().__init__("Member could not be created")


def default_responses() -> dict[int | str, dict[str, Any]]:
    return responses(
        ValidationError,
        RouteNotFoundError,
        MethodNotAllowedError,
        InternalServerError,
    )


def responses(*errors: type[AppBaseException]) -> dict[int | str, dict[str, Any]]:
    """Build a route's `responses=` from the exceptions it can raise."""

    by_status: dict[int, list[type[AppBaseException]]] = {}
    for error in errors:
        by_status.setdefault(error.status, []).append(error)

    result: dict[int | str, dict[str, Any]] = {}
    for status, group in by_status.items():
        models = tuple(error.model() for error in group)
        if len(models) == 1:
            model: Any = models[0]
        else:
            model = Annotated[Union[models], Field(discriminator="error")]  # noqa: UP007
        result[status] = {"model": model}
    return result
