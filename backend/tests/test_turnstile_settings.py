"""Turnstile settings, start-up refusals and deploy files (ADR 0025 sections 7 and 13, PR 1).

Off by default in `Settings` (dev, CI, tests); on, it needs the site key and the secret file; the
fake is refused in prod whatever the switch says, and so are Cloudflare's test keys (by pattern);
the secret is a web-only file, never the environment; `compose.prod.yaml` defaults it on
(Amendment 2)."""

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from authtools import APP_SECRET, ORIGIN
from classbeat.auth.captcha import (
    FakeTurnstileVerifier,
    TurnstileVerifier,
    build_verifier,
    captcha_config,
)
from classbeat.config import (
    ModelProviderRefused,
    Settings,
    check_worker_has_no_turnstile_secret,
)

ROOT = Path(__file__).resolve().parents[2]
REAL_LOOKING_SITE_KEY = "0x4AAAAAAAtestSiteKeyForTests"  # shaped like one; not a real key
REAL_LOOKING_SECRET = "0x4AAAAAAAtestSecretForTestsOnly000000"  # likewise
PROD = {"environment": "prod", "public_base_url": "https://classbeat.example.test"}


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "app_secret_key": APP_SECRET,
        "public_base_url": ORIGIN,
        **overrides,
    }
    return Settings(**values)  # pyright: ignore[reportArgumentType]


def test_off_by_default() -> None:
    plain = settings()
    assert plain.turnstile_enabled is False
    assert plain.turnstile_fake is False
    assert plain.turnstile_site_key is None
    assert plain.turnstile_secret_key is None
    assert build_verifier(plain) is None
    assert captcha_config(plain) is None


def test_on_needs_the_site_key_and_the_secret() -> None:
    with pytest.raises(ValidationError, match="CLASSBEAT_TURNSTILE_SITE_KEY"):
        settings(turnstile_enabled=True, turnstile_secret_key=REAL_LOOKING_SECRET)
    with pytest.raises(ValidationError, match="turnstile_secret_key secret file"):
        settings(turnstile_enabled=True, turnstile_site_key=REAL_LOOKING_SITE_KEY)
    with pytest.raises(ValidationError, match="turnstile_secret_key secret file"):
        settings(
            turnstile_enabled=True,
            turnstile_site_key=REAL_LOOKING_SITE_KEY,
            turnstile_secret_key="   \n",  # the empty placeholder
        )
    real = settings(
        turnstile_enabled=True,
        turnstile_site_key=REAL_LOOKING_SITE_KEY,
        turnstile_secret_key=REAL_LOOKING_SECRET,
    )
    assert isinstance(build_verifier(real), TurnstileVerifier)
    assert captcha_config(real) == {"site_key": REAL_LOOKING_SITE_KEY, "fake": False}
    assert real.turnstile_hostname == "classbeat.test"


def test_the_fake_needs_no_key_and_tells_the_page() -> None:
    fake = settings(turnstile_enabled=True, turnstile_fake=True)
    assert isinstance(build_verifier(fake), FakeTurnstileVerifier)
    assert captcha_config(fake) == {"site_key": None, "fake": True}


@pytest.mark.parametrize("enabled", [True, False])
def test_prod_refuses_the_fake_whatever_the_switch(enabled: bool) -> None:
    with pytest.raises(ValidationError, match="turnstile_fake is not allowed"):
        settings(**PROD, turnstile_enabled=enabled, turnstile_fake=True)


@pytest.mark.parametrize(
    "dummy",
    [
        "1x00000000000000000000AA",  # pass
        "2x00000000000000000000AB",  # fail
        "3x00000000000000000000FF",  # interactive
        "1x00000000000000000000BB",  # invisible
        "1x0000000000000000000000000000000AA",  # the secrets: 31 zeros
        "2x0000000000000000000000000000000AA",
        "3x0000000000000000000000000000000AA",
        "9x0000000000000000ZZ",  # a test key Cloudflare might add later: the pattern refuses it
    ],
)
@pytest.mark.parametrize("enabled", [True, False])
def test_prod_refuses_cloudflares_test_keys_by_pattern(dummy: str, enabled: bool) -> None:
    with pytest.raises(ValidationError, match="Cloudflare test key"):
        settings(
            **PROD,
            turnstile_enabled=enabled,
            turnstile_site_key=dummy,
            turnstile_secret_key=REAL_LOOKING_SECRET,
        )
    with pytest.raises(ValidationError, match="Cloudflare test key"):
        settings(
            **PROD,
            turnstile_enabled=enabled,
            turnstile_site_key=REAL_LOOKING_SITE_KEY,
            turnstile_secret_key=dummy,
        )


def test_test_keys_are_allowed_outside_prod() -> None:
    # Local manual testing may use them (they still need the network, so not in CI or e2e).
    settings(
        turnstile_enabled=True,
        turnstile_site_key="1x00000000000000000000AA",
        turnstile_secret_key="1x0000000000000000000000000000000AA",
    )


def test_prod_starts_with_real_keys_or_with_turnstile_off() -> None:
    settings(
        **PROD,
        turnstile_enabled=True,
        turnstile_site_key=REAL_LOOKING_SITE_KEY,
        turnstile_secret_key=REAL_LOOKING_SECRET,
    )
    settings(**PROD)  # off: the owner's emergency degrade, logged at start (test_turnstile_routes)


@pytest.mark.parametrize("bad", ["has space", "a/b", "x" * 129, "ä"])
def test_the_site_key_is_a_plain_token(bad: str) -> None:
    with pytest.raises(ValidationError, match="turnstile_site_key"):
        settings(turnstile_site_key=bad)


@pytest.mark.parametrize("bad", ["two words", "tab\there", "ü-key", "x" * 1025])
def test_a_malformed_secret_is_refused_without_echoing_it(bad: str) -> None:
    with pytest.raises(ValidationError) as caught:
        settings(turnstile_secret_key=bad)
    assert "turnstile_secret_key" in str(caught.value)
    assert bad not in str(caught.value)


def test_the_secret_comes_only_from_its_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    (tmp_path / "app_secret_key").write_text(APP_SECRET, "utf-8")
    monkeypatch.setenv("CLASSBEAT_TURNSTILE_SECRET_KEY", REAL_LOOKING_SECRET)
    assert Settings().turnstile_secret_key is None  # the environment never sets a secret
    (tmp_path / "turnstile_secret_key").write_text(REAL_LOOKING_SECRET + "\n", "utf-8")
    loaded = Settings()
    assert loaded.turnstile_secret_key is not None
    assert loaded.turnstile_secret_key.get_secret_value() == REAL_LOOKING_SECRET
    assert REAL_LOOKING_SECRET not in repr(loaded)
    (tmp_path / "turnstile_secret_key").write_text("", "utf-8")
    assert Settings().turnstile_secret_key is None  # the empty placeholder


def test_the_switches_and_site_key_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    (tmp_path / "app_secret_key").write_text(APP_SECRET, "utf-8")
    monkeypatch.setenv("CLASSBEAT_TURNSTILE_ENABLED", "true")
    monkeypatch.setenv("CLASSBEAT_TURNSTILE_FAKE", "true")
    monkeypatch.setenv("CLASSBEAT_TURNSTILE_SITE_KEY", "")  # compose's ${TURNSTILE_SITE_KEY:-}
    loaded = Settings()
    assert (loaded.turnstile_enabled, loaded.turnstile_fake) == (True, True)
    assert loaded.turnstile_site_key is None


def test_the_worker_refuses_a_readable_secret() -> None:
    check_worker_has_no_turnstile_secret(settings())
    with pytest.raises(ModelProviderRefused, match="turnstile_secret_key"):
        check_worker_has_no_turnstile_secret(settings(turnstile_secret_key=REAL_LOOKING_SECRET))


def test_a_prod_app_with_turnstile_off_logs_an_error_at_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from fastapi import FastAPI

    from classbeat.auth.setup import attach_state, detach_state

    app = FastAPI()
    prod = settings(**PROD)
    with caplog.at_level(logging.ERROR, logger="classbeat.auth.setup"):
        attach_state(app, prod, None)  # pyright: ignore[reportArgumentType]
    try:
        assert app.state.captcha is None
        assert any(
            r.levelno == logging.ERROR and "Turnstile is off" in r.getMessage()
            for r in caplog.records
        )
    finally:
        detach_state(app)


# --- deploy files ------------------------------------------------------------------------------


def _compose(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / name).read_text("utf-8"))


def _secret_names(service: dict[str, Any]) -> set[str]:
    return {s if isinstance(s, str) else s["source"] for s in service.get("secrets", [])}


@pytest.mark.skipif(not (ROOT / "compose.yaml").is_file(), reason="not a full checkout")
@pytest.mark.parametrize("name", ["compose.yaml", "compose.prod.yaml"])
def test_the_secret_is_a_web_only_file(name: str) -> None:
    compose = _compose(name)
    assert compose["secrets"]["turnstile_secret_key"] == {"file": "./secrets/turnstile_secret_key"}
    holders = [
        n for n, s in compose["services"].items() if "turnstile_secret_key" in _secret_names(s)
    ]
    assert holders == ["web"]


@pytest.mark.skipif(not (ROOT / "compose.yaml").is_file(), reason="not a full checkout")
def test_prod_is_on_by_default_and_never_sets_the_fake() -> None:
    web = _compose("compose.prod.yaml")["services"]["web"]["environment"]
    # ADR 0025 Amendment 2 (#721): on by default in production; false turns it off.
    assert web["CLASSBEAT_TURNSTILE_ENABLED"] == "${TURNSTILE_ENABLED:-true}"
    assert web["CLASSBEAT_TURNSTILE_SITE_KEY"] == "${TURNSTILE_SITE_KEY:-}"
    for name in ("compose.prod.yaml", "compose.prod.loopback.yaml", "compose.prod.lan.yaml"):
        assert "TURNSTILE_FAKE" not in (ROOT / name).read_text("utf-8")
    for service, spec in _compose("compose.prod.yaml")["services"].items():
        if service != "web":
            assert not any(
                k.startswith("CLASSBEAT_TURNSTILE_") for k in spec.get("environment", {})
            )


@pytest.mark.skipif(not (ROOT / "compose.yaml").is_file(), reason="not a full checkout")
def test_the_dev_stack_leaves_it_off() -> None:
    for spec in _compose("compose.yaml")["services"].values():
        assert not any(k.startswith("CLASSBEAT_TURNSTILE_") for k in spec.get("environment", {}))


@pytest.mark.skipif(not (ROOT / "compose.yaml").is_file(), reason="not a full checkout")
def test_the_e2e_stack_turns_on_the_fake_on_web_only() -> None:
    # PR 2 of #721: on together with the widget, so every e2e sign-in goes through the stand-in.
    services = _compose("compose.e2e.yaml")["services"]
    web = services["web"]["environment"]
    assert web["CLASSBEAT_TURNSTILE_ENABLED"] == "true"
    assert web["CLASSBEAT_TURNSTILE_FAKE"] == "true"
    assert "CLASSBEAT_ENVIRONMENT" not in web
    assert "CLASSBEAT_TURNSTILE_SITE_KEY" not in web
    # web-passkeys (ADR 0019 section 11, W7) is the same web image on localhost: the same fake.
    passkeys = services["web-passkeys"]["environment"]
    assert passkeys["CLASSBEAT_TURNSTILE_ENABLED"] == "true"
    assert passkeys["CLASSBEAT_TURNSTILE_FAKE"] == "true"
    assert "CLASSBEAT_TURNSTILE_SITE_KEY" not in passkeys
    # web-paddle (PB-6, #977): the same web image on 127.0.0.1 with the fake Paddle, the same fake.
    paddle = services["web-paddle"]["environment"]
    assert paddle["CLASSBEAT_TURNSTILE_ENABLED"] == "true"
    assert paddle["CLASSBEAT_TURNSTILE_FAKE"] == "true"
    assert "CLASSBEAT_TURNSTILE_SITE_KEY" not in paddle
    for service, spec in services.items():
        if service not in ("web", "web-passkeys", "web-paddle"):
            assert not any(
                k.startswith("CLASSBEAT_TURNSTILE_") for k in spec.get("environment", {})
            )


@pytest.mark.skipif(not (ROOT / "scripts").is_dir(), reason="not a full checkout")
def test_every_placeholder_list_creates_the_file() -> None:
    assert "turnstile_secret_key" in (ROOT / "scripts" / "worktree.sh").read_text("utf-8")
    assert "turnstile_secret_key" in (ROOT / "scripts" / "test-install-check.sh").read_text("utf-8")
    # check-prod-compose.sh is split into sourced parts (#763): read the script and its parts.
    check = "".join(
        f.read_text("utf-8") for f in sorted((ROOT / "scripts").glob("check-prod-compose*"))
    )
    assert "turnstile_secret_key belongs to web only" in check
    assert "CLASSBEAT_TURNSTILE_FAKE is never set" in check
    runbook = (ROOT / "docs" / "runbooks" / "vps-deploy.md").read_text("utf-8")
    assert runbook.count("turnstile_secret_key") >= 3  # the section 5 loop, its note, the update
