"""Turns exceptions that reach the edge of the API into one clean JSON error.

    {"detail": "<safe message>", "code": "<machine readable>"}

`detail` matches what FastAPI's own HTTPException produces, so clients read every error the same way.
Internal text (database hosts, provider messages, stack traces) goes to the log, never to the client.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from helpers.exceptions import DomainError
from helpers.logging_setup import request_id_var

logger = logging.getLogger("uvicorn.error")

DATABASE_DOWN_MESSAGE = "The database is temporarily unavailable. Please try again shortly."
GENERIC_MESSAGE = "Something went wrong. Please try again."


def public_message_for(exc: BaseException) -> str:
    """The text a user may see for any exception (used by handlers and by streaming responses)."""
    if isinstance(exc, DomainError):
        return exc.public_message
    if isinstance(exc, PyMongoError):
        return DATABASE_DOWN_MESSAGE
    return GENERIC_MESSAGE


def _json(status_code: int, message: str, code: str) -> JSONResponse:
    content = {"detail": message, "code": code}
    request_id = request_id_var.get()
    if request_id:
        content["request_id"] = request_id  # what a user quotes to support; it finds the log lines
    return JSONResponse(status_code=status_code, content=content)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError):
        level = logging.WARNING if exc.status_code < 500 else logging.ERROR
        logger.log(level, f"{exc.code} on {request.method} {request.url.path}: {exc}", exc_info=exc.status_code >= 500)
        return _json(exc.status_code, exc.public_message, exc.code)

    @app.exception_handler(PyMongoError)
    async def handle_database_error(request: Request, exc: PyMongoError):
        logger.error(f"database error on {request.method} {request.url.path}", exc_info=exc)
        return _json(503, DATABASE_DOWN_MESSAGE, "database_unavailable")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.error(f"unhandled error on {request.method} {request.url.path}", exc_info=exc)
        return _json(500, GENERIC_MESSAGE, "internal_error")
