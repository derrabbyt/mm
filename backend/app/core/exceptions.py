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


class MissingBearerTokenError(AppBaseException):
    code = "missing_bearer_token"
    status = 401

    def __init__(self):
        super().__init__("Missing bearer token")


class InvalidBearerTokenError(AppBaseException):
    code = "invalid_bearer_token"
    status = 401

    def __init__(self):
        super().__init__("Invalid or expired access token")


class UnauthenticatedRoleError(AppBaseException):
    code = "unauthenticated_role"
    status = 403

    def __init__(self):
        super().__init__("Authenticated account required")


class MeetupNotFoundError(AppBaseException):
    code = "meetup_not_found"
    status = 404

    def __init__(self, meetup_id: str):
        super().__init__(f"Meetup {meetup_id} not found")


class MeetupLoadError(AppBaseException):
    code = "meetup_load_error"
    status = 503

    def __init__(self, meetup_id: str):
        super().__init__(f"Meetup {meetup_id} could not be loaded")


class MeetupsLoadError(AppBaseException):
    code = "meetups_load_error"
    status = 503

    def __init__(self):
        super().__init__("Meetups could not be loaded")


class MeetupCreateError(AppBaseException):
    code = "meetup_create_error"
    status = 503

    def __init__(self):
        super().__init__("Meetup could not be created")


class AccountNotFoundError(AppBaseException):
    code = "account_not_found"
    status = 404

    def __init__(self, account_id: str):
        super().__init__(f"Account {account_id} not found")


class ParticipantNotFoundError(AppBaseException):
    code = "participant_not_found"
    status = 404

    def __init__(self, participant_id: str):
        super().__init__(f"Participant {participant_id} not found")


class ParticipantLoadError(AppBaseException):
    code = "participant_load_error"
    status = 503

    def __init__(self, participant_id: str):
        super().__init__(f"Participant {participant_id} could not be loaded")


class ParticipantsLoadError(AppBaseException):
    code = "participants_load_error"
    status = 503

    def __init__(self):
        super().__init__("Participants could not be loaded")


class ParticipantUpdateError(AppBaseException):
    code = "participant_update_error"
    status = 503

    def __init__(self, participant_id: str):
        super().__init__(f"Participant {participant_id} could not be updated")


class ParticipantCreateError(AppBaseException):
    code = "participant_create_error"
    status = 503

    def __init__(self):
        super().__init__("Participant could not be created")


class NoPositionedParticipantsError(AppBaseException):
    code = "no_positioned_participants"
    status = 422

    def __init__(self):
        super().__init__("No participant has a position set")


class ParticipantOffGridError(AppBaseException):
    code = "participant_off_grid"
    status = 422

    def __init__(self, participant_name: str):
        super().__init__(
            f"{participant_name} is outside the area the travel times cover"
        )


class RendezvousInfeasibleError(AppBaseException):
    code = "rendezvous_infeasible"
    status = 422

    def __init__(self, participant_names: list[str], max_minutes: int):
        stranded = ", ".join(participant_names)
        super().__init__(
            f"No spot is within {max_minutes} minutes of everyone: {stranded} "
            f"cannot reach any candidate in time"
        )


class AccountUpsertError(AppBaseException):
    code = "account_upsert_error"
    status = 503

    def __init__(self):
        super().__init__("Account could not be loaded or created")


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
