import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .exceptions import (
    AppBaseException,
    FieldError,
    InternalServerError,
    MethodNotAllowedError,
    RouteNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

HTTP_ERROR_CODES = {
    e.status: e.code for e in (RouteNotFoundError, MethodNotAllowedError)
}


def _envelope(
    status: int,
    error: str,
    message: str,
    details: list[FieldError] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"error": error, "message": message}
    if details is not None:
        body["details"] = [d.model_dump() for d in details]
    return JSONResponse(status_code=status, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppBaseException)
    async def app_exc_handler(request: Request, exc: AppBaseException) -> JSONResponse:
        if exc.status >= 500:
            logger.error(
                "%s on %s %s:\n%s",
                exc.code,
                request.method,
                request.url.path,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
        return _envelope(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        error = ValidationError(
            [
                FieldError(
                    location=[str(part) for part in item.get("loc", ())],
                    message=item.get("msg", "Invalid value"),
                    type=item.get("type", "validation_error"),
                )
                for item in exc.errors()
            ]
        )
        return _envelope(error.status, error.code, error.message, error.details)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = HTTP_ERROR_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return _envelope(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def catch_all_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "UnhandledException on %s %s:\n%s",
            request.method,
            request.url.path,
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )
        error = InternalServerError()
        return _envelope(error.status, error.code, error.message)
