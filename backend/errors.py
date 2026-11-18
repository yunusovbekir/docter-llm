"""Domain exceptions and their mapping to the JSON error shape.

Every error response has the shape ``{"error": {"code": str, "message": str}}``.
"""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    """Stable machine-readable code and human-readable error message."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """The error envelope shared by domain, HTTP, and validation failures."""

    error: ErrorDetail


class DocterError(Exception):
    """Base class for domain errors exposed through the API."""

    status_code: int = 400
    code: str = "docter_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build a JSON response in the standard error shape."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def _handle_domain_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DocterError)
    return error_response(exc.status_code, exc.code, exc.message)


async def _handle_http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    try:
        code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        code = "http_error"
    response = error_response(exc.status_code, code, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()
    first = errors[0] if errors else {}
    location = ".".join(str(part) for part in first.get("loc", ()))
    detail = first.get("msg", "Invalid request")
    message = f"{location}: {detail}" if location else str(detail)
    return error_response(422, "validation_error", message)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = request.state.request_id
    logger.error(
        "Unhandled error on %s %s (request_id=%s)",
        request.method,
        request.url.path,
        request_id,
        exc_info=exc,
    )
    response = error_response(500, "internal_error", "Internal server error")
    # Starlette's server-error handler runs outside user middleware.
    response.headers["X-Request-ID"] = request_id
    return response


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers that map all errors to the standard JSON shape."""
    app.add_exception_handler(DocterError, _handle_domain_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
