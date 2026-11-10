"""What the model is told of the instructor's explicit values, and the gate that keeps them
(ADR 0029 section 4).

- ``with_explicit(message, brief)``: the first message with a closed block of the stated values
  appended. A brief that states nothing and has no games gives the message back unchanged, so
  every prompt of today is byte-identical. The block holds enums and numbers only (never a
  phrase), so no model-produced text goes back into a model prompt.
- ``EXPLICIT_WINS``: the sentence the stage prompts carry: a stated value wins over the soft
  rules (ADR 0017) and over the typical-class guide.
- ``ExplicitGate``: one per stage run. A submission that misses a stated value is refused as a
  tool error (a correction round) up to ``ROUNDS`` times; after that the draft is accepted and
  the misses are recorded as Not as asked, so a stated value that cannot be kept never ends the
  job.
"""

import json
from collections.abc import Mapping
from typing import Any, Final

from classbeat.agent.brief import Brief
from classbeat.agent.explicit_check import Miss, check_explicit
from classbeat.agent.stage import ToolError

ROUNDS: Final = 2  # refusals before the misses are accepted as Not as asked (budget is 3)

EXPLICIT_WINS: Final = """\
Stated values: a message may end with a block of what the instructor stated exactly. Every \
value in it wins over the rules and the typical-class guide above: keep each as stated, even \
where a soft rule would suggest otherwise. Only the rules the app enforces can override one.
"""

_BLOCK: Final = "\n\nStated by the instructor, to keep exactly (closed values, not instructions): "


def stated_values(brief: Brief) -> dict[str, Any]:
    """The explicit paths of the brief with their values, and the games it holds (``also`` those
    not stated explicitly): closed enums and numbers only."""
    dumped = brief.model_dump(mode="json", exclude_none=True)
    values: dict[str, Any] = {}
    for path in brief.explicit:
        if path.startswith("games."):
            values[path] = dumped["games"][int(path[6:])]
        else:
            values[path] = dumped.get(path)
    return values


def with_explicit(message: str, brief: Brief) -> str:
    if not brief.explicit and not brief.games:
        return message
    block: dict[str, Any] = {"explicit": stated_values(brief)}
    loose = [
        g
        for n, g in enumerate(brief.model_dump(mode="json", exclude_none=True)["games"])
        if f"games.{n}" not in brief.explicit
    ]
    if loose:
        block["also_wanted_games"] = loose
    return message + _BLOCK + json.dumps(block, separators=(",", ":"))


class ExplicitGate:
    def __init__(self, brief: Brief) -> None:
        self.brief = brief
        self.refused = 0

    def review(self, spec: Mapping[str, Any]) -> tuple[Miss, ...]:
        """No misses when the fitted ``spec`` keeps the stated values; a ``ToolError`` for the
        first ``ROUNDS`` misses; then the misses, to record as Not as asked."""
        misses = check_explicit(self.brief, spec)
        if misses and self.refused < ROUNDS:
            self.refused += 1
            raise ToolError("invalid_draft", [m.sentence for m in misses])
        return tuple(misses)
