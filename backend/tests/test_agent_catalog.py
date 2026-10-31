"""The model catalog (ADR 0012 §1): every entry is priced, every priced model is listed, each
entry's host is its provider's one host, and only Anthropic may receive user data until the
owner decides (§6)."""

import dataclasses

import pytest

from classbeat.agent import catalog
from classbeat.agent.catalog import CATALOG, EFFORTS, CatalogModel, find, models_of
from classbeat.agent.prices import DEFAULT_PRICES
from classbeat.config import Settings
from classbeat.egress import MODEL_HOSTS


def test_the_three_anthropic_models_are_listed_with_every_effort() -> None:
    assert [(m.provider, m.model) for m in models_of("anthropic")] == [
        ("anthropic", "claude-opus-5-5"),
        ("anthropic", "claude-sonnet-5-5"),
        ("anthropic", "claude-haiku-5-5"),
    ]
    for entry in models_of("anthropic"):
        assert entry.efforts == EFFORTS
        assert entry.host == "api.anthropic.com"
        assert entry.data_policy == "allowed"


def _unpriced() -> list[tuple[str, str]]:
    return [
        (entry.provider, entry.model)
        for entry in catalog.CATALOG
        if DEFAULT_PRICES.lookup(entry.provider, entry.model) is None
    ]


def _uncatalogued() -> list[tuple[str, str]]:
    return sorted(
        key for key in DEFAULT_PRICES.priced() if key[0] != "fake" and catalog.find(*key) is None
    )


def test_every_catalog_model_has_a_price() -> None:
    assert _unpriced() == []


def test_every_priced_model_is_in_the_catalog_fake_excepted() -> None:
    assert _uncatalogued() == []


def test_the_price_check_fails_on_an_unpriced_catalog_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unpriced = CatalogModel(
        "openai", "gpt-unpriced", ("medium",), MODEL_HOSTS["openai"], "owner_pending"
    )
    monkeypatch.setattr(catalog, "CATALOG", (*CATALOG, unpriced))
    assert _unpriced() == [("openai", "gpt-unpriced")]


def test_the_catalog_check_fails_on_a_priced_model_missing_from_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(catalog, "CATALOG", CATALOG[1:])
    assert _uncatalogued() == [("anthropic", "claude-opus-5-5")]


def test_fake_is_not_in_the_catalog() -> None:
    assert all(entry.provider != "fake" for entry in CATALOG)  # pyright: ignore[reportUnnecessaryComparison]
    assert find("fake", "fake") is None


def test_entries_are_unique() -> None:
    keys = [(entry.provider, entry.model) for entry in CATALOG]
    assert len(keys) == len(set(keys))


def test_each_entry_reaches_its_providers_one_host() -> None:
    for entry in CATALOG:
        assert entry.host == MODEL_HOSTS[entry.provider]


def test_only_anthropic_may_receive_user_data() -> None:
    """ADR 0012 §6: OpenAI's and DeepSeek's entries ship ``owner_pending``. Changing one to
    ``allowed`` is the owner's decision (questions 1 and 2), recorded in ADR 0010."""
    for entry in CATALOG:
        if entry.provider != "anthropic":
            assert entry.data_policy == "owner_pending", entry


def test_the_default_agent_model_is_in_the_catalog() -> None:
    settings = Settings()
    entry = find("anthropic", settings.agent_model)
    assert entry is not None
    assert settings.agent_effort in entry.efforts


def test_find_is_exact() -> None:
    assert find("anthropic", "Claude-Opus-5-5") is None
    assert find("anthropic", "claude-opus-5-5 ") is None
    assert find("openai", "claude-opus-5-5") is None


BASE = CatalogModel("openai", "gpt-x", ("low",), "api.openai.com", "owner_pending")


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"provider": "fake"}, "provider"),
        ({"provider": "OpenAI"}, "provider"),
        ({"model": ""}, "model id"),
        ({"model": "gpt x"}, "model id"),
        ({"model": "a" * 101}, "model id"),
        ({"efforts": ()}, "effort"),
        ({"efforts": ("low", "low")}, "effort"),
        ({"efforts": ("turbo",)}, "effort"),
        ({"host": "api.deepseek.com"}, "host"),  # another provider's host
        ({"host": "api.anthropic.com"}, "host"),
        ({"host": "API.openai.com"}, "host"),
        ({"data_policy": "maybe"}, "data policy"),
    ],
)
def test_a_malformed_entry_is_refused(change: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        dataclasses.replace(BASE, **change)  # pyright: ignore[reportArgumentType]


def test_a_deepseek_entry_must_name_the_deepseek_host() -> None:
    CatalogModel("deepseek", "deepseek-x", ("medium",), "api.deepseek.com", "owner_pending")
    with pytest.raises(ValueError, match="host"):
        CatalogModel("deepseek", "deepseek-x", ("medium",), "api.openai.com", "owner_pending")
