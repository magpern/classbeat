"""The billing provider flag and its start-up gates (ADR 0032 section 4, #973), the webhook's
in-memory gate, and the CSRF allowlist entry. No database, no network."""

import os
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from authtools import APP_SECRET, ORIGIN
from classbeat.auth.middleware import CSRF_EXEMPT_PATHS, SIGNED_PATHS
from classbeat.billing import webhook
from classbeat.billing.webhook_gate import MAX_BODY, Busy, TooLarge, WebhookLimiter, read_body
from classbeat.config import (
    ModelProviderRefused,
    Settings,
    check_worker_has_no_paddle_secret,
)
from classbeat.config_billing import provider_of, webhook_secrets
from classbeat.web import create_app

SECRET = "pdl_ntfset_test_secret"
PROD = {"environment": "prod", "public_base_url": "https://classbeat.example.test"}


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test", "app_secret_key": APP_SECRET, "public_base_url": ORIGIN,
        **overrides,
    }  # fmt: skip
    return Settings(**values)  # pyright: ignore[reportArgumentType]


def test_dummy_is_the_default_and_needs_no_secret() -> None:
    made = settings()
    assert made.billing_provider == "dummy"
    assert provider_of(made) is None
    assert webhook_secrets(made) == []


def test_the_fake_runs_with_its_secret_outside_prod() -> None:
    made = settings(billing_provider="fake_paddle", paddle_webhook_secret=f"{SECRET}\nold-one\n")
    assert provider_of(made) == "fake_paddle"
    assert webhook_secrets(made) == [SECRET, "old-one"]
    assert SECRET not in repr(made)


def test_paddle_is_refused_until_the_real_client_exists() -> None:
    with pytest.raises(ValidationError, match="real Paddle client is not written"):
        settings(billing_provider="paddle", paddle_webhook_secret=SECRET)


@pytest.mark.parametrize("secret", [SECRET, None])
def test_the_fake_is_refused_in_prod(secret: str | None) -> None:
    with pytest.raises(ValidationError, match="fake_paddle is not allowed when environment is"):
        settings(billing_provider="fake_paddle", paddle_webhook_secret=secret, **PROD)


@pytest.mark.parametrize("secret", [None, "", "  \n"])
def test_the_fake_needs_the_secret_file(secret: str | None) -> None:
    with pytest.raises(ValidationError, match="needs the paddle_webhook_secret secret file"):
        settings(billing_provider="fake_paddle", paddle_webhook_secret=secret)


@pytest.mark.parametrize("bad", ["a b", "é-secret", "x\ty", "1\n2\n3\n4\n5", "x" * 257])
def test_a_malformed_secret_is_refused_without_its_content(bad: str) -> None:
    with pytest.raises(ValidationError) as caught:
        settings(paddle_webhook_secret=bad)
    assert "printable ASCII" in str(caught.value)
    assert bad not in str(caught.value)


def test_billing_on_stays_refused() -> None:
    with pytest.raises(ValidationError, match="no real billing provider"):
        settings(billing_enabled=True, billing_provider="fake_paddle", paddle_webhook_secret=SECRET)


def test_the_secret_comes_from_its_file_and_never_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_PADDLE_WEBHOOK_SECRET", "from-the-environment")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    assert Settings().paddle_webhook_secret is None
    (tmp_path / "paddle_webhook_secret").write_text(f"{SECRET}\n")
    found = Settings().paddle_webhook_secret
    assert found is not None
    assert found.get_secret_value() == SECRET
    assert os.environ["CLASSBEAT_PADDLE_WEBHOOK_SECRET"] == "from-the-environment"


def test_the_worker_refuses_a_readable_secret() -> None:
    with pytest.raises(ModelProviderRefused, match="web only"):
        check_worker_has_no_paddle_secret(settings(paddle_webhook_secret=SECRET))
    check_worker_has_no_paddle_secret(settings())


def test_the_webhook_is_on_the_csrf_allowlist_by_its_exact_path_only() -> None:
    assert webhook.PATH == "/api/billing/paddle/webhook"
    assert {webhook.PATH} == SIGNED_PATHS
    assert {
        "/api/mail/unsubscribe", "/api/mail/unsubscribe/confirm", webhook.PATH
    } == CSRF_EXEMPT_PATHS  # fmt: skip


async def _post_unsigned(made: Settings) -> int:
    """No lifespan, so no database exists: the route must answer before any is needed."""
    transport = httpx.ASGITransport(app=create_app(made))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        return (await client.post(webhook.PATH, content=b"{}")).status_code


async def test_the_route_exists_only_for_the_fake() -> None:
    assert await _post_unsigned(settings()) in {404, 405}
    fake = settings(billing_provider="fake_paddle", paddle_webhook_secret=SECRET)
    assert await _post_unsigned(fake) == 401  # mounted, and no Origin or CSRF token is asked for


def test_the_limit_counts_per_network_and_for_the_process() -> None:
    limiter = WebhookLimiter(per_ip=2, total=3)
    assert [limiter.allow("a", now=0) for _ in range(3)] == [True, True, False]
    assert not limiter.allow("b", now=1)  # the process' total is used up
    assert limiter.allow("a", now=60)  # a new window


def test_a_full_network_table_does_not_reset_the_process_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("classbeat.billing.webhook_gate.TRACKED", 3)
    limiter = WebhookLimiter(per_ip=10, total=6)
    # Three networks fill the table; the fourth clears it (not the total), then two more are
    # allowed and the seventh request of the window is refused whichever network it comes from.
    assert [limiter.allow(n, now=0) for n in ("a", "b", "c", "d", "e", "f")] == [True] * 6
    assert not limiter.allow("g", now=0)
    assert not limiter.allow("a", now=0)
    assert limiter.allow("a", now=60)  # a new window starts both counts again


def test_a_known_network_never_clears_the_full_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("classbeat.billing.webhook_gate.TRACKED", 2)
    limiter = WebhookLimiter(per_ip=2, total=100)
    assert [limiter.allow(n, now=0) for n in ("a", "b", "a")] == [True, True, True]
    assert not limiter.allow("a", now=0)  # its own count survived the full table


def test_the_semaphore_never_waits() -> None:
    busy = Busy()
    assert busy.take()
    assert not busy.take()
    busy.release()
    assert busy.take()


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


async def test_the_body_is_read_raw_under_its_cap() -> None:
    assert await read_body(_chunks(b'{"a":', b" 1}")) == b'{"a": 1}'
    with pytest.raises(TooLarge):
        await read_body(_chunks(b"x" * MAX_BODY, b"y"))
