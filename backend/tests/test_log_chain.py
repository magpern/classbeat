"""The JSON formatter's DBAPIError redaction walks the exception chain (review of #46)."""

import io
import json
import logging

import pytest
from fastapi import BackgroundTasks, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError, OperationalError

from classbeat.log import configure_logging, find_database_error, redact

SECRET = "victim@example.com"


class FakeDriverError(Exception):
    sqlstate = "23505"


def database_error() -> IntegrityError:
    orig = FakeDriverError(f"Key (email_normalized)=({SECRET}) already exists.")
    return IntegrityError("INSERT ...", {"p": SECRET}, orig)


def logged(error: BaseException) -> str:
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    try:
        raise error
    except BaseException:
        logging.getLogger("classbeat.test").exception("failed")
    line = json.loads(stream.getvalue().splitlines()[-1])
    return line["exc"]


@pytest.mark.usefixtures("restore_logging")
def test_a_direct_database_error_is_class_and_sqlstate_only() -> None:
    text = logged(database_error())
    assert text == "IntegrityError (SQLSTATE 23505)"


@pytest.mark.usefixtures("restore_logging")
def test_the_cause_chain_is_walked() -> None:
    try:
        raise database_error()
    except IntegrityError as exc:
        wrapped = RuntimeError("Caught handled exception, but response already started.")
        wrapped.__cause__ = exc
    text = logged(wrapped)
    assert SECRET not in text
    assert "SQLSTATE 23505" in text
    assert "RuntimeError" in text


@pytest.mark.usefixtures("restore_logging")
def test_the_context_chain_is_walked() -> None:
    try:
        try:
            raise database_error()
        except IntegrityError:
            raise ValueError("while handling") from None
    except ValueError as exc:
        text = logged(exc)
    assert SECRET not in text
    assert "SQLSTATE 23505" in text


@pytest.mark.usefixtures("restore_logging")
def test_exception_group_members_are_walked() -> None:
    group = ExceptionGroup(
        "task group", [ValueError("x"), OperationalError("s", {}, Exception(SECRET))]
    )
    text = logged(group)
    assert SECRET not in text
    assert "OperationalError" in text


def test_a_cycle_in_the_chain_ends() -> None:
    a, b = ValueError("a"), ValueError("b")
    a.__cause__, b.__cause__ = b, a
    assert find_database_error(a) is None


def test_other_errors_keep_their_traceback(restore_logging: None) -> None:
    assert "ValueError: boom" in logged(ValueError("boom"))


def test_tokens_in_urls_are_redacted() -> None:
    assert "abc123" not in redact("opened https://x.test/reset#token=abc123 today")
    assert "abc123" not in redact("GET /x?token=abc123&y=1")


@pytest.mark.usefixtures("restore_logging")
async def test_a_database_error_raised_in_a_background_task_is_redacted() -> None:
    """Starlette re-raises an error after the response started; the formatter must still
    find the DBAPIError under it."""
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    app = FastAPI()

    async def task() -> None:
        raise database_error()

    @app.post("/boom")
    async def boom(background: BackgroundTasks) -> dict[str, bool]:
        background.add_task(task)
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        try:
            await client.post("/boom")
        except Exception:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    out = stream.getvalue()
    assert "Exception in ASGI application" in out
    assert SECRET not in out
    assert "SQLSTATE 23505" in out
