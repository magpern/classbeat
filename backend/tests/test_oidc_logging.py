"""No query string, body, cookie or token of ``/oidc/*`` reaches a log (ADR 0034 section 1, ADR
0003 section 10; #1056 criterion 3). Every logger at DEBUG, formatted with the production
``JsonFormatter``, over a whole flow, its refusals and the redaction patterns themselves."""

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from authtools import Env
from classbeat.log import QUIET_LOGGERS, JsonFormatter
from oidctools import CLIENT_SECRET, FakePlayer, basic, challenge_of, flow_cookie_of, oidc_env


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.handler.setFormatter(JsonFormatter())
    caplog.set_level(logging.DEBUG)
    for name in QUIET_LOGGERS:
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


@pytest.fixture
async def oenv(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    async with oidc_env(app_database_url, kek_dir) as running:
        yield running


async def test_a_whole_flow_logs_no_query_body_cookie_or_token(
    oenv: Env, logs: pytest.LogCaptureFixture
) -> None:
    user_id = await oenv.user()
    session = await oenv.login(user_id)
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    assert flow is not None
    shown = await player.follow(started.headers["location"], flow, session.token)
    request_id = shown.headers["location"].split("r=")[1]
    given = await player.consent(request_id, flow, session.token)
    code = given.json()["redirect"].split("code=")[1].split("&")[0]
    issued = (await player.token(code)).json()
    await player.token(code)  # refused: used
    await player.token("nope", headers=basic(secret="y" * 43))  # refused: client
    await FakePlayer(oenv).authorize(prompt="none")  # refused: a redirect with an error
    await player.follow(started.headers["location"], None, session.token)  # refused: no cookie

    text = logs.text
    assert logs.records, "the access log ran"
    planted = (
        player.verifier, challenge_of(player.verifier), player.state, player.nonce, code, flow,
        session.token, CLIENT_SECRET, issued["id_token"], issued["access_token"], request_id,
    )  # fmt: skip
    for value in planted:
        assert value not in text
    for line in text.splitlines():
        record = json.loads(line)
        path = str(record.get("path", ""))
        assert "?" not in path
    assert "/oidc/token" in text  # the path itself is logged, nothing after it


@pytest.mark.parametrize(
    "text",
    [
        "GET /oidc/authorize?state=PLANTEDSTATE&nonce=PLANTEDNONCE 302",
        "GET /oidc/authorize/continue?r=PLANTEDREQ 400",
        "Cookie: __Host-cb_oidc=PLANTEDCOOKIE",
        "{'__Host-cb_oidc': 'PLANTEDCOOKIE'}",
        "grant_type=authorization_code&code=PLANTEDCODE&code_verifier=PLANTEDVERIFIER",
        json.dumps({"id_token": "PLANTEDTOKEN", "nonce": "PLANTEDNONCE"}),
        "id_token=PLANTEDTOKEN&code_challenge=PLANTEDCHALLENGE",
    ],
)
def test_the_redaction_masks_oidc_text(logs: pytest.LogCaptureFixture, text: str) -> None:
    logger = logging.getLogger("classbeat.auth.oidc.test")
    logger.warning(text)
    logger.warning("field", extra={"detail": text})
    logger.warning("keys", extra={"id_token": "PLANTEDTOKEN", "nonce": "PLANTEDNONCE"})
    assert "PLANTED" not in logs.text
