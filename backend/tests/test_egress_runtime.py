"""The runtime half of the outbound guard (ADR 0006 section 9): once the worker's allowlist is
installed, the web-only call sites the static guard exempts (OAuth sign-in, account mail) refuse
before any connection. And the lazy ``classbeat.auth`` package that keeps them out of the worker
process exports exactly what it did."""

import ast
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from classbeat import auth, egress
from classbeat.auth import oauth_client
from classbeat.auth.oauth_providers import Provider
from classbeat.config import Settings
from classbeat.egress import OutboundBlocked, install_worker_allowlist
from classbeat.mail import Mailer, MailMessage

PROVIDER = Provider(
    name="google",
    client_id="test-client",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    scope="openid email",
    jwks_url="https://www.googleapis.com/oauth2/v3/certs",
    client_secret="public-test-value",
)


class NoSmtp:
    """Stands in for ``smtplib.SMTP``: records a connection attempt, never connects."""

    calls: ClassVar[list[str]] = []

    def __init__(self, host: str, *_: Any, **__: Any) -> None:
        NoSmtp.calls.append(host)
        raise ConnectionRefusedError("test: no SMTP")


@pytest.fixture
def no_smtp(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    NoSmtp.calls = []
    monkeypatch.setattr("classbeat.mail.smtplib.SMTP", NoSmtp)
    return NoSmtp.calls


def _mailer() -> Mailer:
    return Mailer(Settings(smtp_host="mailpit", smtp_port=1025, smtp_tls="none"))


MESSAGE = MailMessage(to="someone@example.com", subject="s", body="b")


async def test_mail_is_refused_in_the_worker_before_any_connection(no_smtp: list[str]) -> None:
    install_worker_allowlist()
    with pytest.raises(OutboundBlocked) as raised:
        await _mailer().send(MESSAGE)
    assert raised.value.host == "mailpit"
    with pytest.raises(OutboundBlocked):
        _mailer()._send_blocking(MESSAGE)  # pyright: ignore[reportPrivateUsage]
    assert no_smtp == []


async def test_mutation_without_the_worker_flag_mail_would_connect(no_smtp: list[str]) -> None:
    """The same call outside the worker reaches the SMTP client: the refusal above is the flag."""
    assert not egress.worker_allowlist_installed()
    with pytest.raises(Exception, match="delivery failed"):
        await _mailer().send(MESSAGE)
    assert no_smtp == ["mailpit"]


async def test_oauth_is_refused_in_the_worker_before_any_client() -> None:
    seen: list[str] = []
    transport = httpx.MockTransport(lambda r: seen.append(str(r.url)) or httpx.Response(200))
    install_worker_allowlist()
    with pytest.raises(OutboundBlocked) as raised:
        await oauth_client._fetch_jwks_with(transport, PROVIDER.jwks_url or "")  # pyright: ignore[reportPrivateUsage]
    assert raised.value.host == "www.googleapis.com"
    with pytest.raises(OutboundBlocked) as raised:
        oauth_client._client(PROVIDER, "https://classbeat.example/cb", transport)  # pyright: ignore[reportPrivateUsage]
    assert raised.value.host == "oauth2.googleapis.com"
    assert seen == []


async def test_mutation_without_the_worker_flag_oauth_would_fetch() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"keys": []})

    await oauth_client._fetch_jwks_with(httpx.MockTransport(answer), PROVIDER.jwks_url or "")  # pyright: ignore[reportPrivateUsage]
    assert seen == [PROVIDER.jwks_url]
    assert oauth_client._client(PROVIDER, "https://x.example/cb", None) is not None  # pyright: ignore[reportPrivateUsage]


def test_the_worker_flag_is_set_only_by_installing_the_allowlist() -> None:
    assert not egress.worker_allowlist_installed()
    egress.refuse_in_worker("anything.example")  # no-op outside the worker
    install_worker_allowlist()
    assert egress.worker_allowlist_installed()
    with pytest.raises(OutboundBlocked):
        egress.refuse_in_worker("anything.example")


# --- the lazy auth package -----------------------------------------------------------------

INIT = Path(auth.__file__)


def _type_checking_imports() -> dict[str, str]:
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.If):
            for item in node.body:
                if isinstance(item, ast.ImportFrom) and item.module:
                    for alias in item.names:
                        names[alias.asname or alias.name] = item.module.rsplit(".", 1)[-1]
    return names


def test_the_lazy_exports_match_the_type_checking_imports_and_all() -> None:
    exports: dict[str, str] = auth._EXPORTS
    assert exports == _type_checking_imports()
    assert set(exports) == set(auth.__all__)


@pytest.mark.parametrize("name", sorted(auth.__all__))
def test_every_export_resolves_to_its_module_object(name: str) -> None:
    exports: dict[str, str] = auth._EXPORTS
    module = __import__(f"classbeat.auth.{exports[name]}", fromlist=[name])
    assert getattr(auth, name) is getattr(module, name)


def test_submodules_and_unknown_names() -> None:
    assert auth.tables.__name__ == "classbeat.auth.tables"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(AttributeError):
        _ = auth.no_such_name  # pyright: ignore[reportAttributeAccessIssue]
