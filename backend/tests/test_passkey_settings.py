"""Passkey settings, the start-up check and the relying party (ADR 0019 sections 2 and 3, W1 #771).

``CLASSBEAT_PASSKEYS`` is off by default; on, it needs ``CLASSBEAT_WEBAUTHN_RP_ID`` (owner answer
6), and the RP ID must be a lowercase DNS name, no IP, port, path or trailing dot, equal to the
public host or a parent of it. The RP ID and the expected origin come from settings only: a forged
``Host`` or ``X-Forwarded-Host`` changes neither. The pinned py_webauthn keeps the API names the ADR
builds on.
"""

import inspect
import json
from importlib.metadata import version

import httpx
import pytest
import webauthn
from fastapi import FastAPI, Request
from pydantic import ValidationError
from webauthn.helpers import options_to_json

from authtools import APP_SECRET
from classbeat.config import Settings
from classbeat.config_passkeys import (
    PasskeySettingsRefused,
    check_passkey_settings,
    relying_party,
    rp_id_problem,
)
from classbeat.web import create_app

PROD = "https://classbeat.eu"
BETA = "https://classbeat.pernemark.se"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "app_secret_key": APP_SECRET,
        "public_base_url": BETA,
        **overrides,
    }
    return Settings(**values)  # pyright: ignore[reportArgumentType]


def test_off_by_default_and_on_needs_the_rp_id() -> None:
    plain = settings()
    assert (plain.passkeys, plain.webauthn_rp_id) == (False, None)
    with pytest.raises(ValidationError, match="passkeys needs CLASSBEAT_WEBAUTHN_RP_ID"):
        settings(passkeys=True)


@pytest.mark.parametrize(
    ("base_url", "rp_id"),
    [
        (PROD, "classbeat.eu"),  # production (owner, 2026-11-05)
        (BETA, "classbeat.pernemark.se"),  # test and beta (owner, 2026-11-07)
        ("https://classbeat.eu:8443", "classbeat.eu"),  # the port is the origin's, not the RP ID's
        ("https://app.classbeat.eu", "classbeat.eu"),  # a parent is allowed by the check
        ("http://localhost:8080", "localhost"),  # the e2e stack's web-passkeys (section 11)
        ("http://app.localhost:8080", "app.localhost"),
    ],
)
def test_accepted(base_url: str, rp_id: str) -> None:
    assert rp_id_problem(rp_id, base_url) is None
    on = settings(public_base_url=base_url, passkeys=True, webauthn_rp_id=rp_id)
    assert relying_party(on).rp_id == rp_id


@pytest.mark.parametrize(
    ("base_url", "rp_id", "why"),
    [
        ("http://127.0.0.1:8000", "127.0.0.1", "IP address"),
        ("http://10.0.0.1", "10.0.0.1", "IP address"),
        ("http://127.1", "127.1", "IP address"),  # a browser reads this as 127.0.0.1
        ("http://[::1]:8000", "::1", "lowercase DNS name"),
        (BETA, "classbeat.pernemark.se:443", "lowercase DNS name"),  # a port
        (BETA, "classbeat.pernemark.se.", "lowercase DNS name"),  # a trailing dot
        (BETA, "https://classbeat.pernemark.se", "lowercase DNS name"),  # a scheme
        (BETA, "classbeat.pernemark.se/x", "lowercase DNS name"),  # a path
        (BETA, "Classbeat.Pernemark.SE", "lowercase DNS name"),
        (BETA, "classbeat..pernemark.se", "lowercase DNS name"),
        (BETA, "", "lowercase DNS name"),
        (BETA, "classbeat.eu", "host of public_base_url"),  # an unrelated host
        (PROD, "classbeat.pernemark.se", "host of public_base_url"),
        (BETA, "evilclassbeat.pernemark.se", "host of public_base_url"),
        (BETA, "beat.pernemark.se", "host of public_base_url"),  # a suffix, not a dot-suffix
        (BETA, "se", "two labels"),
        ("http://myhost:8000", "myhost", "two labels"),
    ],
)
def test_refused(base_url: str, rp_id: str, why: str) -> None:
    problem = rp_id_problem(rp_id, base_url)
    assert problem is not None and why in problem, problem  # noqa: PT018
    with pytest.raises(PasskeySettingsRefused, match=why):
        check_passkey_settings(True, rp_id, base_url)


def test_settings_refuse_a_bad_rp_id_at_start_whatever_the_flag() -> None:
    with pytest.raises(ValidationError, match="IP address"):
        settings(public_base_url="http://127.0.0.1:8000", passkeys=True, webauthn_rp_id="127.0.0.1")
    with pytest.raises(ValidationError, match="host of public_base_url"):
        settings(passkeys=False, webauthn_rp_id="classbeat.eu")
    # An IP base URL with passkeys on cannot pass (section 11): no RP ID matches it.
    for rp_id in ("127.0.0.1", "localhost"):
        with pytest.raises(ValidationError):
            settings(public_base_url="http://127.0.0.1:8000", passkeys=True, webauthn_rp_id=rp_id)


def test_the_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_PASSKEYS", "true")
    monkeypatch.setenv("CLASSBEAT_WEBAUTHN_RP_ID", "classbeat.pernemark.se")
    read = settings()
    assert (read.passkeys, read.webauthn_rp_id) == (True, "classbeat.pernemark.se")


def test_the_relying_party_is_the_rp_id_and_the_exact_public_origin() -> None:
    party = relying_party(settings(public_base_url="HTTPS://Classbeat.EU:443/"))
    assert (party.rp_id, party.origin) == ("classbeat.eu", PROD)
    on = settings(public_base_url=PROD, passkeys=True, webauthn_rp_id="classbeat.eu")
    assert (relying_party(on).rp_id, relying_party(on).origin) == ("classbeat.eu", PROD)


FORGED = [
    {},
    {"Host": "evil.example"},
    {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"},
    {"Host": "evil.example", "X-Forwarded-Host": "pernemark.se", "Origin": "https://evil.example"},
    {"Forwarded": "host=evil.example;proto=http", "X-Forwarded-For": "203.0.113.9"},
]


async def test_forged_host_headers_change_neither_the_rp_id_nor_the_origin() -> None:
    """The real web app, behind a trusted proxy (whose forwarded headers are believed for the client
    address): a probe route builds registration options the way W2 will, from ``relying_party`` of
    the app's settings. Every forged header set gives the same RP ID and origin."""
    app: FastAPI = create_app(
        settings(
            passkeys=True, webauthn_rp_id="classbeat.pernemark.se", trusted_proxies="0.0.0.0/0"
        )
    )

    @app.get("/passkey-probe")
    def probe(request: Request) -> dict[str, object]:  # pyright: ignore[reportUnusedFunction]
        party = relying_party(request.app.state.settings)
        options = webauthn.generate_registration_options(
            rp_id=party.rp_id, rp_name="classbeat", user_name="a@example.com", user_id=bytes(32)
        )
        return {"rp": json.loads(options_to_json(options))["rp"], "origin": party.origin}

    # The SPA fallback and the API routes are registered first; the probe goes in front of them.
    app.router.routes.insert(0, app.router.routes.pop())
    seen = []
    transport = httpx.ASGITransport(app=app, client=("10.0.0.2", 1234))
    async with httpx.AsyncClient(transport=transport, base_url=BETA) as client:
        for headers in FORGED:
            answer = await client.get("/passkey-probe", headers=headers)
            assert answer.status_code == 200, answer.text
            seen.append(answer.json())
    assert seen == [
        {"rp": {"id": "classbeat.pernemark.se", "name": "classbeat"}, "origin": BETA}
    ] * len(FORGED)


def test_the_pinned_library_keeps_the_names_the_adr_uses() -> None:
    """ADR 0019 section 2: ``webauthn==3.0.1`` and the keyword names the ceremonies call."""
    assert version("webauthn") == "3.0.1"

    def keywords(name: str) -> set[str]:
        return set(inspect.signature(getattr(webauthn, name)).parameters)

    assert {"rp_id", "rp_name", "user_id", "user_name", "user_display_name", "challenge",
            "timeout", "attestation", "authenticator_selection", "exclude_credentials",
            "supported_pub_key_algs"} <= keywords("generate_registration_options")  # fmt: skip
    assert {"rp_id", "challenge", "timeout", "allow_credentials", "user_verification"} <= keywords(
        "generate_authentication_options"
    )
    assert {"credential", "expected_challenge", "expected_rp_id", "expected_origin",
            "require_user_verification", "supported_pub_key_algs",
            "pem_root_certs_bytes_by_fmt"} <= keywords("verify_registration_response")  # fmt: skip
    assert {"credential", "expected_challenge", "expected_rp_id", "expected_origin",
            "credential_public_key", "credential_current_sign_count",
            "require_user_verification"} <= keywords("verify_authentication_response")  # fmt: skip
    # No trust anchors unless given (attestation is not used).
    default = inspect.signature(webauthn.verify_registration_response).parameters
    assert default["pem_root_certs_bytes_by_fmt"].default is None
