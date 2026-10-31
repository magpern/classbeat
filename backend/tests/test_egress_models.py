"""Per-provider egress (ADR 0012 §2): ``MODEL_HOSTS``, each model client's one-host list, and the
worker's urllib list holding a model host only for a provider it can use (its key file present;
DeepSeek also needs ``CLASSBEAT_DEEPSEEK_ENABLED``)."""

import urllib.request
from pathlib import Path

import httpx
import pytest

from classbeat import egress
from classbeat.config import Settings, worker_model_providers
from classbeat.egress import (
    MODEL_ALLOWLISTS,
    MODEL_HOSTS,
    OutboundBlocked,
    allowlisted_async_client,
    worker_allowlist,
)
from classbeat.worker import __main__ as worker_main

OTHERS = {"api.mowl.com", "api.spotify.com", "accounts.spotify.com"}
# Planted values shaped like keys; never sent anywhere.
PLANTED = {
    "anthropic": "sk-ant-planted-0123",
    "openai": "sk-proj-planted-4567",
    "deepseek": "sk-ds-89ab",
}


def _key(directory: Path, provider: str, value: str | None = None) -> None:
    (directory / f"{provider}_api_key").write_text(PLANTED[provider] if value is None else value)


def test_the_model_hosts_are_exactly_the_three_providers_apis() -> None:
    assert dict(MODEL_HOSTS) == {
        "anthropic": "api.anthropic.com",
        "openai": "api.openai.com",
        "deepseek": "api.deepseek.com",
    }
    with pytest.raises(TypeError):
        MODEL_HOSTS["openai"] = "evil.example"  # pyright: ignore[reportIndexIssue]


@pytest.mark.parametrize("provider", sorted(MODEL_HOSTS))
def test_each_providers_list_is_its_one_host(provider: str) -> None:
    allowlist = MODEL_ALLOWLISTS[provider]  # pyright: ignore[reportArgumentType]
    assert allowlist.hosts == frozenset({MODEL_HOSTS[provider]})  # pyright: ignore[reportArgumentType]
    for other, host in MODEL_HOSTS.items():
        if other != provider:
            with pytest.raises(OutboundBlocked):
                allowlist.check("https", host, 443, False)


def test_the_openai_list_never_admits_the_deepseek_host_or_the_other_way_round() -> None:
    with pytest.raises(OutboundBlocked):
        MODEL_ALLOWLISTS["openai"].check_url("https://api.deepseek.com/chat/completions")
    with pytest.raises(OutboundBlocked):
        MODEL_ALLOWLISTS["deepseek"].check_url("https://api.openai.com/v1/responses")


class Recorder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})


@pytest.mark.parametrize("provider", sorted(MODEL_HOSTS))
async def test_a_providers_client_reaches_its_host_and_no_other_model_host(provider: str) -> None:
    recorder = Recorder()
    own = MODEL_HOSTS[provider]  # pyright: ignore[reportArgumentType]
    async with allowlisted_async_client(
        provider=provider,  # pyright: ignore[reportArgumentType]
        inner=httpx.MockTransport(recorder),
    ) as client:
        assert (await client.post(f"https://{own}/v1/x", json={})).status_code == 200
        for host in {*MODEL_HOSTS.values(), *OTHERS} - {own}:
            with pytest.raises(OutboundBlocked):
                await client.post(f"https://{host}/v1/x", json={})
    assert recorder.seen == [f"https://{own}/v1/x"]


async def test_the_default_client_is_anthropics() -> None:
    recorder = Recorder()
    async with allowlisted_async_client(inner=httpx.MockTransport(recorder)) as client:
        with pytest.raises(OutboundBlocked):
            await client.get("https://api.openai.com/v1/models")
        await client.get("https://api.anthropic.com/v1/models")
    assert recorder.seen == ["https://api.anthropic.com/v1/models"]


def test_an_unknown_provider_gets_no_client() -> None:
    for name in ("fake", "OpenAI", "evil.example"):
        with pytest.raises(ValueError, match="unknown"):
            allowlisted_async_client(provider=name)  # pyright: ignore[reportArgumentType]
        with pytest.raises(ValueError, match="unknown"):
            worker_allowlist([name])


def test_the_worker_list_without_a_usable_provider_has_no_model_host() -> None:
    assert worker_allowlist().hosts == frozenset(OTHERS)
    assert not worker_allowlist().hosts & set(MODEL_HOSTS.values())


def test_the_worker_list_adds_exactly_the_usable_providers_hosts() -> None:
    assert worker_allowlist(["openai"]).hosts == frozenset({*OTHERS, "api.openai.com"})
    assert worker_allowlist(["deepseek", "anthropic"]).hosts == frozenset(
        {*OTHERS, "api.deepseek.com", "api.anthropic.com"}
    )


# --- which providers the worker can use, from its settings ---------------------------------


def test_no_key_file_no_provider(kek_dir: Path) -> None:
    assert worker_model_providers(Settings()) == frozenset()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_a_key_file_makes_its_provider_usable(kek_dir: Path, provider: str) -> None:
    _key(kek_dir, provider)
    assert worker_model_providers(Settings()) == {provider}


@pytest.mark.parametrize("provider", sorted(MODEL_HOSTS))
@pytest.mark.parametrize("content", ["", "\n", "  \r\n"])
def test_an_empty_key_file_counts_as_none(kek_dir: Path, provider: str, content: str) -> None:
    _key(kek_dir, provider, content)
    assert worker_model_providers(Settings(deepseek_enabled=True)) == frozenset()


def test_deepseek_needs_its_key_and_the_setting(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Settings().deepseek_enabled is False  # the default
    assert worker_model_providers(Settings(deepseek_enabled=True)) == frozenset()  # no key
    _key(kek_dir, "deepseek")
    assert worker_model_providers(Settings()) == frozenset()  # setting off
    monkeypatch.setenv("CLASSBEAT_DEEPSEEK_ENABLED", "true")
    assert worker_model_providers(Settings()) == {"deepseek"}


def test_the_openai_host_is_on_the_worker_list_only_with_its_key(kek_dir: Path) -> None:
    hosts = worker_allowlist(worker_model_providers(Settings())).hosts
    assert "api.openai.com" not in hosts
    _key(kek_dir, "openai")
    hosts = worker_allowlist(worker_model_providers(Settings())).hosts
    assert "api.openai.com" in hosts
    assert "api.deepseek.com" not in hosts
    assert "api.anthropic.com" not in hosts


# --- the worker entry point installs that list --------------------------------------------


def _field(obj: object, name: str) -> object:
    return getattr(obj, name)


@pytest.mark.usefixtures("restore_logging")
@pytest.mark.parametrize(
    ("keys", "enabled", "expected"),
    [
        ((), False, frozenset()),
        (("openai",), False, frozenset({"openai"})),
        (("deepseek",), False, frozenset()),
        (("deepseek",), True, frozenset({"deepseek"})),
        (("anthropic", "openai", "deepseek"), False, frozenset({"anthropic", "openai"})),
    ],
)
def test_the_worker_installs_the_model_hosts_of_its_usable_providers(
    kek_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
    enabled: bool,
    expected: frozenset[str],
) -> None:
    for provider in keys:
        _key(kek_dir, provider)
    monkeypatch.setenv("CLASSBEAT_MODEL_PROVIDER", "fake")
    if enabled:
        monkeypatch.setenv("CLASSBEAT_DEEPSEEK_ENABLED", "true")
    monkeypatch.setattr(urllib.request, "_opener", None)
    installed: list[frozenset[str]] = []
    real = egress.install_worker_allowlist

    def recording(model_providers: object = ()) -> urllib.request.OpenerDirector:
        installed.append(frozenset(model_providers))  # pyright: ignore[reportArgumentType]
        return real(model_providers)  # pyright: ignore[reportArgumentType]

    async def no_run(_: Settings) -> None:
        return None

    monkeypatch.setattr(worker_main, "install_worker_allowlist", recording)
    monkeypatch.setattr(worker_main, "_run", no_run)
    assert worker_main.main() == 0
    assert installed == [frozenset(), expected]  # none before the settings, then the usable ones
    opener = _field(urllib.request, "_opener")
    assert isinstance(opener, urllib.request.OpenerDirector)
    for provider, host in MODEL_HOSTS.items():
        if provider not in expected:
            with pytest.raises(OutboundBlocked):
                opener.open(f"https://{host}/", timeout=1)
