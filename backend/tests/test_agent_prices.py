"""The price table's Anthropic entries (ADR 0006 section 2, issue #134): Opus, Sonnet and Haiku
5.5, Haiku's tier at a 100 000 token prompt, and the estimates that never use the lower tier."""

from decimal import Decimal

import pytest

from classbeat.agent.prices import DEFAULT_PRICES, HAIKU_TIER_TOKENS, Price
from classbeat.agent.provider import Usage

SONNET = DEFAULT_PRICES.lookup("anthropic", "claude-sonnet-5-5")
HAIKU = DEFAULT_PRICES.lookup("anthropic", "claude-haiku-5-5")


def test_sonnet_has_the_published_prices() -> None:
    assert Price(Decimal(2), Decimal(10), Decimal("2.5"), Decimal("0.2")) == SONNET


def test_sonnet_cost_arithmetic() -> None:
    usage = Usage(input_tokens=1000, output_tokens=200, cache_read_tokens=3, cache_write_tokens=10)
    assert SONNET is not None
    assert SONNET.cost_micros(usage) == 2000 + 2000 + 25 + 1  # 0.6 for the cache read, rounded up
    assert SONNET.output_micros(24_000) == 240_000
    assert SONNET.prompt_micros(1000) == 2500  # at the dearer cache-write rate


def test_haiku_has_two_tiers_with_cache_prices_derived_from_each_input_price() -> None:
    assert HAIKU is not None
    assert HAIKU.above is not None
    assert (HAIKU.input, HAIKU.output) == (Decimal("0.1"), Decimal("0.5"))
    assert (HAIKU.cache_read, HAIKU.cache_write) == (Decimal("0.01"), Decimal("0.125"))
    up = HAIKU.above
    assert (up.input, up.output) == (Decimal("0.5"), Decimal("2.5"))
    assert (up.cache_read, up.cache_write) == (Decimal("0.05"), Decimal("0.625"))
    assert HAIKU.above_tokens == HAIKU_TIER_TOKENS == 100_000


def test_a_prompt_of_100000_tokens_is_the_lower_tier_and_one_more_is_the_upper() -> None:
    assert HAIKU is not None
    assert HAIKU.cost_micros(Usage(input_tokens=100_000, output_tokens=1000)) == 10_000 + 500
    assert HAIKU.cost_micros(Usage(input_tokens=100_001, output_tokens=1000)) == 50_001 + 2500


def test_the_tier_counts_input_cache_read_and_cache_write_together() -> None:
    assert HAIKU is not None
    low = Usage(input_tokens=99_000, cache_write_tokens=1000)  # 100 000 in all
    assert HAIKU.cost_micros(low) == 9900 + 125
    high = Usage(input_tokens=1, cache_read_tokens=100_000)  # 100 001 in all
    assert HAIKU.cost_micros(high) == 1 + 5000  # 0.5 + 5000, rounded up
    cached = Usage(cache_read_tokens=100_000)
    assert HAIKU.cost_micros(cached) == 1000


def test_estimates_and_ceilings_use_the_upper_tier() -> None:
    assert HAIKU is not None
    # Its tokenizer counts about 30 % more than we estimate: never assume the lower tier.
    assert HAIKU.prompt_micros(1000) == 625  # the upper tier's cache write, the dearest input rate
    assert HAIKU.output_micros(1000) == 2500


def test_a_flat_price_has_one_tier() -> None:
    flat = Price(Decimal(1), Decimal(2), Decimal(3), Decimal(4))
    assert flat.dearest is flat
    assert flat.tier_for(10**9) is flat


@pytest.mark.parametrize(
    ("above", "tokens"),
    [(None, 5), (Price(Decimal(1), Decimal(1), Decimal(1), Decimal(1)), 0)],
)
def test_a_tier_needs_both_its_bound_and_its_rates(above: Price | None, tokens: int) -> None:
    with pytest.raises(ValueError, match="tier"):
        Price(Decimal(1), Decimal(1), Decimal(1), Decimal(1), above_tokens=tokens, above=above)


def test_a_price_has_at_most_two_tiers() -> None:
    inner = Price(Decimal(1), Decimal(1), Decimal(1), Decimal(1))
    middle = Price(Decimal(1), Decimal(1), Decimal(1), Decimal(1), 10, inner)
    with pytest.raises(ValueError, match="two tiers"):
        Price(Decimal(1), Decimal(1), Decimal(1), Decimal(1), 5, middle)


def test_only_the_three_models_are_priced_and_no_wildcard_reaches_anthropic() -> None:
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
        assert DEFAULT_PRICES.lookup("anthropic", model) is not None
    assert DEFAULT_PRICES.lookup("anthropic", "claude-haiku-5-5-20990101") is None
    assert DEFAULT_PRICES.lookup("anthropic", "claude-unpriced-1") is None
