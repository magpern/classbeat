"""The catalog of models classbeat may use (ADR 0012 §1), as reviewed code.

The catalog is the only source for "which provider and model exist": the eval command's
``--model``, a route, the price lookup and the egress allowlist all read it (M3c G1, EV5 wire
them). Every entry has a price in ``classbeat.agent.prices`` and every priced model (``fake``
excepted) is in the catalog (``tests/test_agent_catalog.py``).

- ``host`` is the one API host its provider's client may reach (``classbeat.egress.MODEL_HOSTS``).
- ``efforts`` are the classbeat efforts the model accepts; an effort it does not list is refused
  when a route or an eval run is set up, never silently changed (ADR 0012 §3).
- ``data_policy`` (ADR 0012 §6): ``allowed`` may receive user data; ``owner_pending`` is
  evaluated on synthetic data only and no route may use it. Anthropic's entries are ``allowed``
  (ADR 0010, Amendment 1). OpenAI's and DeepSeek's entries ship ``owner_pending`` until the owner
  decides (ADR 0012 owner questions 1 and 2); their entries and prices are added by their
  adapter issues (M3c P2, P3) from the providers' published pages, marked "verify before live
  use", with a new ``PRICES_VERSION``.

``fake`` is not in the catalog: it stays a worker setting for dev and CI.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

from classbeat.egress import MODEL_HOSTS, ModelProviderName

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORTS: Final[tuple[Effort, ...]] = ("low", "medium", "high", "xhigh", "max")
DataPolicy = Literal["allowed", "owner_pending"]
# ADR 0012 §6: the providers whose entries may be ``allowed`` (the owner accepted sending them user
# data, ADR 0010). Another provider's entry is ``owner_pending`` by construction; adding one here
# is the owner's decision (ADR 0012 owner questions 1 and 2), with its dated line in ADR 0010.
USER_DATA_PROVIDERS: Final = frozenset({"anthropic"})
# The same rule as ``CLASSBEAT_AGENT_MODEL`` and ``model_eval_results.model`` (ADR 0012 §5).
MODEL_ID: Final = re.compile(r"[A-Za-z0-9._:-]{1,100}")


@dataclass(frozen=True, slots=True)
class CatalogModel:
    provider: ModelProviderName
    model: str  # the provider's model id
    efforts: tuple[Effort, ...]  # classbeat efforts it accepts (mapped per provider, §3)
    host: str  # the one API host its client may reach
    data_policy: DataPolicy  # §6

    def __post_init__(self) -> None:
        if self.provider not in MODEL_HOSTS:
            raise ValueError("unknown model provider")
        if MODEL_ID.fullmatch(self.model) is None:
            raise ValueError("a model id matches ^[A-Za-z0-9._:-]{1,100}$")
        if not self.efforts or len(set(self.efforts)) != len(self.efforts):
            raise ValueError("a model lists at least one effort, each once")
        if any(effort not in EFFORTS for effort in self.efforts):
            raise ValueError("unknown effort")
        if self.host != MODEL_HOSTS[self.provider]:
            raise ValueError("a model's host is its provider's one host")
        if self.data_policy not in ("allowed", "owner_pending"):
            raise ValueError("unknown data policy")
        if self.data_policy == "allowed" and self.provider not in USER_DATA_PROVIDERS:
            raise ValueError("only a provider the owner accepted may receive user data")


def _anthropic(model: str) -> CatalogModel:
    # All three accept every effort through ``output_config.effort`` (the claude-api skill's
    # thinking and effort table, cached 2026-10-06; verify before live use, as the prices).
    return CatalogModel("anthropic", model, EFFORTS, MODEL_HOSTS["anthropic"], "allowed")


CATALOG: Final[tuple[CatalogModel, ...]] = (
    _anthropic("claude-opus-5-5"),
    _anthropic("claude-sonnet-5-5"),
    _anthropic("claude-haiku-5-5"),
)


def find(provider: str, model: str) -> CatalogModel | None:
    """The catalog entry for ``provider`` and ``model`` (exact, case-sensitive), if any."""
    for entry in CATALOG:
        if entry.provider == provider and entry.model == model:
            return entry
    return None


def models_of(provider: str) -> tuple[CatalogModel, ...]:
    """The catalog entries of ``provider``, in catalog order."""
    return tuple(entry for entry in CATALOG if entry.provider == provider)
