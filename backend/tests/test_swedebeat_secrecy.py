"""SwedeBeat connect: no secret in a log, an event, an error body or an exception chain (ADR 0035
sections 2 and 3; threat model T23), and the old app key's settings, which stay until SG-4 removes
them (ADR 0035 section 8). Fakes only, no network.

The secrets searched for: the password (right and wrong), the gateway's bearers, the client key
and the device id the login sent.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from authtools import Env, running_env
from classbeat import connections
from classbeat.config import (
    ModelProviderRefused,
    Settings,
    check_worker_has_no_swedebeat_app,
    swedebeat_app_keys,
)
from classbeat.connections import ConnectionFailure
from classbeat.connections.swedebeat_account import (
    SwedebeatRefusal,
    connect_swedebeat,
    device_id_for,
)
from classbeat.log import redact
from ctx import make_context
from gatewaycore import LOGIN_PATH, FakeGateway
from gatewayfake import CLIENT_KEY, EMAIL, GATEWAY_SETTINGS, PASSWORD, calls
from preftools import use_swedebeat
from swedebeatfake import make_app_key
from test_mowl_redaction import Everything, captured

__all__ = ["captured"]  # fixture
CONNECT = "/api/connections/swedebeat"
WRONG_PASSWORD = "wrong-swedebeat-password-Wd5"
pytestmark = pytest.mark.usefixtures("gateway_api")


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> Any:
    async with running_env(app_database_url, swedebeat_enabled=True, **GATEWAY_SETTINGS) as running:
        yield running


def secrets_of(fake: FakeGateway) -> list[str]:
    values = [PASSWORD, WRONG_PASSWORD, CLIENT_KEY, *fake.bearers]
    values += [r.body["device_id"] for r in calls(fake, LOGIN_PATH)]
    return values


async def test_no_secret_in_logs_events_or_responses_on_any_path(
    env: Env, make_actor: Any, gateway_api: FakeGateway, captured: Everything
) -> None:
    user_id = await env.user("user@example.com")
    await use_swedebeat(env.factory, user_id)  # #525: the user's own switch
    actor = await make_actor(user_id)
    texts: list[str] = []
    sent = {"email": EMAIL, "password": PASSWORD, "force": True}
    steps: list[tuple[dict[str, Any], str | None, int]] = [
        ({"password": WRONG_PASSWORD}, None, 400),
        ({}, None, 200),
        ({}, "unknown_client", 503),
        ({}, "status", 503),  # a 502
        ({"force": False}, None, 409),
        ({}, None, 200),
    ]
    for more, injected, status in steps:
        if injected is not None:
            gateway_api.inject(injected, LOGIN_PATH)
        response = await actor.post(CONNECT, {**sent, **more})
        assert response.status_code == status, response.text
        texts.append(response.text + str(dict(response.headers)))
    texts.append((await actor.get("/api/connections")).text)
    assert (await actor.call("DELETE", CONNECT)).status_code == 204
    events = await env.scalar(
        "SELECT coalesce(json_agg(detail), '[]'::json)::text FROM auth_events"
    )
    texts.append(str(events))
    texts.append(captured.out.getvalue())
    for record in captured.records:
        texts += [repr(vars(record)), record.getMessage()]
    logged = captured.out.getvalue()
    assert "swedebeat" in logged  # the capture saw the flow
    blob = "\n".join(texts)
    # Three bearers were issued (the 502 replaced one login's answer); none is anywhere.
    assert len(gateway_api.bearers) == 3
    for secret in secrets_of(gateway_api):
        assert secret not in blob, secret[:6]


async def test_failures_carry_the_category_alone_and_no_chain(
    env: Env, gateway_api: FakeGateway
) -> None:
    user_id = await env.user("user@example.com")
    ctx = make_context(user_id)
    ring = env.app.state.keyring
    secret = b"x" * 32
    key = SecretStr(CLIENT_KEY)

    async def attempt(password: str, **more: Any) -> BaseException:
        values: dict[str, Any] = {"force": True, "device_secret": secret, "key": key, **more}
        with pytest.raises((ConnectionFailure, SwedebeatRefusal)) as caught:
            await connect_swedebeat(env.factory, ring, ctx, EMAIL, password, **values)
        return caught.value

    wrong = await attempt(WRONG_PASSWORD)
    assert isinstance(wrong, ConnectionFailure) and wrong.args == ("invalid_credentials",)  # noqa: PT018
    gateway_api.inject("unknown_client", LOGIN_PATH)
    refused = await attempt(PASSWORD)
    assert isinstance(refused, SwedebeatRefusal) and refused.args == ("app_key_unavailable",)  # noqa: PT018
    gateway_api.inject("status", LOGIN_PATH, status=502)
    down = await attempt(PASSWORD)
    assert isinstance(down, ConnectionFailure) and down.args == ("upstream_unavailable",)  # noqa: PT018
    too_long = await attempt("p" * 257)  # outside the gateway's bounds: never sent
    assert isinstance(too_long, ConnectionFailure) and too_long.args == ("invalid_credentials",)  # noqa: PT018
    for error in (wrong, refused, down, too_long):
        assert error.__cause__ is None
        assert error.__context__ is None
        text = repr(error) + str(error.args)
        assert WRONG_PASSWORD not in text and CLIENT_KEY not in text  # noqa: PT018
    sent = len(gateway_api.requests)
    assert sent == 3
    for more in ({"key": None}, {"force": False}):
        with pytest.raises(SwedebeatRefusal) as none:
            await connect_swedebeat(
                env.factory, ring, ctx, EMAIL, PASSWORD,
                **{"force": True, "device_secret": secret, "key": key, **more},
            )  # fmt: skip
        expected = "app_key_unavailable" if "key" in more else "sign_out_required"
        assert none.value.args == (expected,)
    assert len(gateway_api.requests) == sent  # nothing was sent without the key or the consent
    assert await connections.connection_refs(env.factory, ctx) == frozenset()


def test_the_device_id_is_a_keyed_hash_of_the_user() -> None:
    import uuid

    first, other = uuid.uuid4(), uuid.uuid4()
    assert device_id_for(b"a" * 32, first) == device_id_for(b"a" * 32, first)
    assert device_id_for(b"a" * 32, first) != device_id_for(b"a" * 32, other)
    assert device_id_for(b"a" * 32, first) != device_id_for(b"b" * 32, first)
    assert len(device_id_for(b"a" * 32, first)) == 16


def test_the_log_redaction_masks_the_swedebeat_names() -> None:
    from classbeat.log import SECRET_KEYS

    for name in (
        "swedebeat_token",
        "swedebeat_device_id",
        "swedebeat_app",
        "deviceid",
        "password",
        "x-auth-token",
        "x-api-key",
        "bearer",
        "swedebeat_gateway_key",
    ):
        assert name in SECRET_KEYS
    planted = "planted-swedebeat-catalogue-token"
    masked = redact(f"X-Auth-Token: {planted}")
    assert planted not in masked
    assert "[redacted]" in masked
    assert redact("plain text") == "plain text"


# --- the app key's settings ---------------------------------------------------------------


def _app_text(app: dict[str, str]) -> str:
    return json.dumps(app)


def test_the_app_key_file_is_validated_and_masked() -> None:
    app = make_app_key()
    assert swedebeat_app_keys(SecretStr(_app_text(app))) == app
    for bad in (
        "",
        "not json",
        "[]",
        json.dumps({"key": app["key"]}),
        json.dumps({"key": "short", "iv": app["iv"]}),
        json.dumps({"key": app["key"], "iv": "x" * 17}),
        json.dumps({**app, "extra": "1"}),
        json.dumps({"key": 1, "iv": 2}),
        json.dumps({"key": "€" * 32, "iv": app["iv"]}),
    ):
        assert swedebeat_app_keys(SecretStr(bad)) is None
    shown = repr(Settings(swedebeat_app=SecretStr(_app_text(app))))
    assert app["key"] not in shown
    assert app["iv"] not in shown


def test_a_malformed_app_key_file_refuses_the_settings_without_echoing_it() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(swedebeat_app=SecretStr('{"key": "short-secret-value", "iv": "x"}'))
    assert "short-secret-value" not in str(caught.value)


def test_an_empty_placeholder_is_no_key() -> None:
    assert Settings(swedebeat_app=SecretStr("")).swedebeat_app is None
    assert Settings(swedebeat_app=SecretStr("  \n")).swedebeat_keys() is None


def test_the_secret_wins_over_the_image_file_and_a_bad_image_file_is_no_key(tmp_path: Path) -> None:
    secret_app, image_app = make_app_key(), make_app_key()
    image = tmp_path / "swedebeat_app.json"
    image.write_text(_app_text(image_app))
    both = Settings(swedebeat_app=SecretStr(_app_text(secret_app)), swedebeat_app_file=image)
    assert both.swedebeat_keys() == secret_app
    only_image = Settings(swedebeat_app_file=image)
    assert only_image.swedebeat_keys() == image_app
    image.write_text("garbage")
    assert Settings(swedebeat_app_file=image).swedebeat_keys() is None
    assert Settings(swedebeat_app_file=tmp_path / "missing.json").swedebeat_keys() is None


def test_the_secret_file_in_the_secrets_directory_sets_the_app_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app_key()
    (tmp_path / "swedebeat_app").write_text(_app_text(app) + chr(10))
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    assert Settings().swedebeat_keys() == app


def test_the_environment_cannot_set_the_app_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_APP", _app_text(make_app_key()))
    assert Settings().swedebeat_app is None


def test_the_worker_refuses_to_start_with_the_app_key_secret() -> None:
    check_worker_has_no_swedebeat_app(Settings())
    with pytest.raises(ModelProviderRefused):
        check_worker_has_no_swedebeat_app(
            Settings(swedebeat_app=SecretStr(_app_text(make_app_key())))
        )
