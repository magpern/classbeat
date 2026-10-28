"""The MOWL password (and the login ticket and token) never appear in logs, request logs,
exceptions, events or the database (ADR 0004 sections 4, 5 and 7; AGENTS.md security rules).

Every connect outcome runs with the JSON formatter on the root logger at DEBUG; the formatted
output, every record's ``extra`` fields, the stored rows and the raised exceptions (with their
whole ``__cause__``/``__context__`` chain) are searched.
"""

import io
import json
import logging
import zipfile
from collections.abc import Iterator
from importlib import metadata, resources
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from authtools import Env
from classbeat.connections import ConnectionFailure
from classbeat.connections.mowl_account import connect_mowl
from classbeat.db import user_session
from classbeat.log import JsonFormatter
from ctx import make_context
from mowlfake import EMAIL, PASSWORD, TICKET, TOKEN, FakeMowl

CONNECT = "/api/connections/mowl"
SECRETS = (PASSWORD, TICKET, TOKEN)
VENDORED = Path(__file__).parents[1] / "vendor" / "mowl_workouts-0.4.1-py3-none-any.whl"


class Everything(logging.Handler):
    """Keeps every record, formatted by the production formatter as well."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self.out = io.StringIO()
        self.formatter_ = JsonFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.out.write(self.formatter_.format(record) + "\n")


@pytest.fixture
def captured() -> Iterator[Everything]:
    handler = Everything()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


def assert_clean(text: str, *, token_allowed: bool = False) -> None:
    for value in SECRETS:
        if value == TOKEN and token_allowed:
            continue
        assert value not in text, f"leaked {value[:6]}..."


def assert_records_clean(handler: Everything) -> None:
    assert_clean(handler.out.getvalue())
    for record in handler.records:
        # Every attribute, ``extra`` fields included, and the raw message arguments.
        assert_clean(repr(vars(record)))
        assert_clean(record.getMessage())


def chain(error: BaseException | None) -> list[BaseException]:
    found: list[BaseException] = []
    while error is not None and error not in found:
        found.append(error)
        error = error.__cause__ or error.__context__
    return found


@pytest.mark.parametrize(
    ("setup", "status"),
    [
        ({}, 200),
        ({"password": "wrong"}, 400),
        ({"authenticate": 401}, 400),
        ({"authenticate": 429}, 429),
        ({"exchange": 401}, 400),
        ({"exchange": 503}, 503),
        ({"exchange": 418}, 502),
    ],
)
async def test_no_secret_in_logs_access_log_events_or_responses(
    env: Env,
    make_actor: Any,
    mowl_api: FakeMowl,
    captured: Everything,
    setup: dict[str, Any],
    status: int,
) -> None:
    password = setup.pop("password", PASSWORD)
    mowl_api.status.update(setup)
    user_id = await env.user("user@example.com")
    actor = await make_actor(user_id)
    response = await actor.post(CONNECT, {"email": EMAIL, "password": password})
    assert response.status_code == status
    assert_clean(response.text)
    assert_clean(json.dumps(dict(response.headers)))
    assert mowl_api.passwords_seen == [password]  # it did reach (fake) MOWL, and only there
    assert_records_clean(captured)
    assert any(r.name == "classbeat.access" for r in captured.records)  # the request log ran
    events = await env.scalar("SELECT coalesce(json_agg(e)::text, '') FROM auth_events e")
    assert_clean(str(events))
    limits = await env.scalar("SELECT coalesce(json_agg(r)::text, '') FROM rate_limits r")
    assert_clean(str(limits))
    assert EMAIL not in str(limits)  # bucket keys are HMACs
    async with user_session(env.factory, make_context(user_id)) as db:  # RLS: as the owner
        rows = (
            await db.execute(text("SELECT coalesce(json_agg(c)::text, '') FROM connections c"))
        ).scalar_one()
    assert bool(rows) == (status == 200)
    assert_clean(str(rows))  # bytea as hex: ciphertext only
    assert EMAIL not in str(rows)  # the MOWL email is not stored


@pytest.mark.parametrize("setup", [{"password": "wrong"}, {"exchange": 401}, {"exchange": 503}])
async def test_the_failure_has_no_chain_and_holds_no_secret(
    env: Env, mowl_api: FakeMowl, captured: Everything, setup: dict[str, Any]
) -> None:
    password = setup.pop("password", PASSWORD)
    mowl_api.status.update(setup)
    ctx = make_context(await env.user("user@example.com"))
    with pytest.raises(ConnectionFailure) as caught:
        await connect_mowl(env.factory, env.app.state.keyring, ctx, EMAIL, password)
    failure = caught.value
    assert failure.__cause__ is None and failure.__context__ is None  # noqa: PT018
    assert failure.args == (failure.category,)
    for error in chain(failure):
        assert_clean(repr(error))
        assert_clean(str(error))
        assert_clean(repr(error.args))
        assert_clean(repr(vars(error)))
    # Logging the failure with its traceback (as an error handler would) shows no secret: the
    # formatter prints class, message and line numbers only, never frame locals.
    logging.getLogger("test").error("connect failed", exc_info=failure)
    assert_records_clean(captured)


async def test_an_unexpected_library_error_is_not_chained(
    env: Env, mowl_api: FakeMowl, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mowl import api

    def explode(email: str, password: str) -> str:
        raise ValueError(f"bad answer for {password} with ticket {TICKET}")

    monkeypatch.setattr(api, "login", explode)
    ctx = make_context(await env.user("user@example.com"))
    with pytest.raises(ConnectionFailure) as caught:
        await connect_mowl(env.factory, env.app.state.keyring, ctx, EMAIL, PASSWORD)
    assert caught.value.args == ("unknown",)
    assert caught.value.__cause__ is None and caught.value.__context__ is None  # noqa: PT018


def test_the_vendored_wheel_has_no_swedebeat_key() -> None:
    """ADR 0001 and the threat model: classbeat installs mowl-workouts without the bundled
    Swedebeat app key (scripts/build-mowl-wheel.sh)."""
    names = zipfile.ZipFile(VENDORED).namelist()
    assert "mowl/api.py" in names
    assert not [n for n in names if "swedebeat_app" in n]
    assert metadata.version("mowl-workouts") == "0.4.1"
    assert not resources.files("mowl").joinpath("_swedebeat_app.json").is_file()


def test_the_search_finds_a_planted_leak(captured: Everything) -> None:
    """The checks above can fail: a password in an ``extra`` field or a message is caught."""
    logging.getLogger("test").info("plain", extra={"note": f"x{PASSWORD}x"})
    with pytest.raises(AssertionError):
        assert_records_clean(captured)
    with pytest.raises(AssertionError):
        assert_clean(repr(ValueError(TICKET)))
