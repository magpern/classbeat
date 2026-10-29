"""The price table (ADR 0006 section 2): US dollars per million tokens, per provider and model.

Cost is kept in micro-dollars: ``tokens * dollars_per_million`` is micro-dollars exactly, rounded
up to a whole one. A model the table doesn't price is an operator problem (``model_config``).

The fake provider's entry is invented numbers for the tests and the dev stack, shaped like a
large model's (output dearer than input, cache reads cheapest), so the fake scripts' usage numbers
exercise the cost ceiling of section 5. M5 makes the table admin-editable.

**Anthropic (A2): verify before live use.** The entries below are Anthropic's first-party API list
prices as given in the claude-api skill's model table (cached 2026-10-06), not read from a bill or
the live pricing page: Claude Opus 5.5 at $4 input, $20 output, $0.20 cache read per million
tokens, and the 5-minute cache write at 1.25 times input ($5). The provider only asks for 5-minute
(``ephemeral``) cache entries; a 1-hour write (2 times input) is never requested. Check them
against https://platform.claude.com/docs/en/about-claude/pricing before live activation (roadmap
M3b) and edit them here. Only the configured default model is priced: a model the table doesn't
price fails the run ``model_config`` before any request, so a new model is priced (and passes the
section 11 suite) before it is used.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from classbeat.agent.provider import Usage

ANY_MODEL: Final = "*"


@dataclass(frozen=True, slots=True)
class Price:
    """Dollars per million tokens."""

    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal

    def __post_init__(self) -> None:
        if min(self.input, self.output, self.cache_write, self.cache_read) < 0:
            raise ValueError("a price is at least 0")

    def cost_micros(self, usage: Usage) -> int:
        micros = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_write_tokens * self.cache_write
            + usage.cache_read_tokens * self.cache_read
        )
        return math.ceil(micros)

    def prompt_micros(self, tokens: int) -> int:
        """``tokens`` sent as input, at the dearest input rate (an estimate never undershoots)."""
        return math.ceil(tokens * max(self.input, self.cache_write))

    def output_micros(self, tokens: int) -> int:
        return math.ceil(tokens * self.output)


FAKE_PRICE: Final = Price(Decimal(4), Decimal(20), Decimal(5), Decimal("0.4"))


class PriceTable:
    def __init__(self, prices: Mapping[tuple[str, str], Price]) -> None:
        self._prices = dict(prices)

    def lookup(self, provider: str, model: str) -> Price | None:
        return self._prices.get((provider, model)) or self._prices.get((provider, ANY_MODEL))


# Verify before live use (see above).
ANTHROPIC_PRICES: Final = {
    ("anthropic", "claude-opus-5-5"): Price(
        input=Decimal(4), output=Decimal(20), cache_write=Decimal(5), cache_read=Decimal("0.2")
    ),
}

DEFAULT_PRICES: Final = PriceTable({("fake", ANY_MODEL): FAKE_PRICE, **ANTHROPIC_PRICES})
