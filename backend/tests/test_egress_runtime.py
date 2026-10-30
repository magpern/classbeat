"""The runtime half of the outbound guard (ADR 0006 section 9): once the worker's allowlist is
installed, the web-only call sites the static guard exempts (OAuth sign-in, account mail) refuse
before any connection. And the lazy ``classbeat.auth`` package that keeps them out of the worker
process exports exactly what it did."""

import ast
import urllib.request
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
from spotifycore import FakeSpotify
from spotifyfake import CLIENT_A, USER_A, SpotifyHTTPS, spotify_client

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


# --- Spotify (ADR 0009 §10) -----------------------------------------------------------------


async def test_the_spotify_client_keeps_its_own_list_inside_the_worker(
    spotify: FakeSpotify,
) -> None:
    """The worker's urllib list also holds MOWL and the model; the Spotify client never does."""
    install_worker_allowlist()
    access, _ = spotify.grant(CLIENT_A, USER_A)
    async with spotify_client(spotify) as client:
        me = await client.get(
            "https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert me.json()["id"] == USER_A
        for url in ("https://api.mowl.com/v1/programs", "https://api.anthropic.com/v1/x"):
            with pytest.raises(OutboundBlocked):
                await client.get(url)
    assert [s.path for s in spotify.requests()] == ["/v1/me"]


async def test_the_spotify_client_works_and_refuses_alike_in_the_web_process(
    spotify: FakeSpotify,
) -> None:
    assert not egress.worker_allowlist_installed()
    access, _ = spotify.grant(CLIENT_A, USER_A)
    async with spotify_client(spotify) as client:
        me = await client.get(
            "https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert me.status_code == 200
        with pytest.raises(OutboundBlocked):
            await client.get("https://api.mowl.com/v1/programs")
        with pytest.raises(OutboundBlocked):
            await client.get("http://accounts.spotify.com/api/token")


def test_the_installed_worker_opener_lets_the_spotify_hosts_through(
    monkeypatch: pytest.MonkeyPatch, spotify: FakeSpotify
) -> None:
    """The real installed opener (its network handler swapped for the fake): the library's
    urllib calls to Spotify pass the guard, another host does not."""
    opener = install_worker_allowlist()
    every = getattr(opener, "handlers")  # noqa: B009 - not in the type stubs
    handlers = [h for h in every if isinstance(h, egress.DeadlineHTTPSHandler)]
    assert len(handlers) == 1
    fake = SpotifyHTTPS(spotify)
    monkeypatch.setattr(handlers[0], "https_open", fake.https_open)
    access, _ = spotify.grant(CLIENT_A, USER_A)
    request = urllib.request.Request(
        "https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {access}"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        assert b"rider-one" in response.read()
    with pytest.raises(OutboundBlocked):
        urllib.request.urlopen("https://open.spotify.com/track/x", timeout=5)
    assert [s.path for s in spotify.requests()] == ["/v1/me"]


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


def test_dotted_and_private_names_answer_attribute_error_not_import_error() -> None:
    for name in ("tables.metadata", "no.such.module", "_private", "no_such_name"):
        assert not hasattr(auth, name), name
        with pytest.raises(AttributeError):
            getattr(auth, name)


def test_dir_lists_the_lazy_exports() -> None:
    exports: dict[str, str] = auth._EXPORTS
    assert set(exports) <= set(dir(auth))
    assert "tables" not in exports  # a submodule stays found by name, not listed


async def test_identify_keeps_an_outbound_refusal_out_of_provider_error() -> None:
    """``_client`` refuses first in the worker today; this pins the category if a refusal ever
    comes from inside the exchange (a JWKS fetch): it is not turned into ``ProviderError``."""
    from types import SimpleNamespace

    from classbeat.egress import OutboundBlocked

    class Refused:
        async def __aenter__(self) -> "Refused":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def fetch_token(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise OutboundBlocked("oauth2.googleapis.com")

    runtime = SimpleNamespace(transport=None, jwks=None)
    real = oauth_client._client  # pyright: ignore[reportPrivateUsage]
    oauth_client._client = lambda *args: Refused()  # pyright: ignore[reportPrivateUsage]
    try:
        with pytest.raises(OutboundBlocked):
            await oauth_client.identify(
                runtime,  # pyright: ignore[reportArgumentType]
                PROVIDER,
                "https://classbeat.example/cb",
                code="c",
                verifier="v",
                nonce="n",
            )
    finally:
        oauth_client._client = real  # pyright: ignore[reportPrivateUsage]
