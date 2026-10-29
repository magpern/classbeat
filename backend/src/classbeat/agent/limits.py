"""Per-run limits (ADR 0006 section 5) and the cost check.

The loop checks each limit at its checkpoints, before the call that would exceed it:

| Limit | Default | When hit |
|---|---|---|
| Model turns per stage | 12 | ``limit_exceeded`` |
| Tool calls per stage | 40 | ``limit_exceeded`` |
| Correction rounds (``submit_*`` refused) | 3 | ``invalid_output`` on the next refusal |
| Wall time per stage | 300 s | ``limit_exceeded`` |
| Cost per job, across stages and attempts | $1.50 | ``limit_exceeded`` |

**The cost check** runs before each turn: what the job has spent (every earlier run of the job,
any stage and attempt, plus this run so far), plus the price of the conversation so far sent as
input, plus a per-turn output reserve (``agent_turn_output_reserve`` tokens, default 8 000,
thinking included) must not exceed the ceiling. A turn can therefore overshoot the ceiling by at
most ``max_tokens`` minus the reserve, once.

The conversation's size is estimated from what the loop knows without a tokenizer: the last
request's prompt tokens as the provider reported them, plus its output, plus the characters added
since (tool results, the reminder) at one token per ``CHARS_PER_TOKEN`` characters; the first
request is estimated from its characters alone. Three characters per token overestimates English
and JSON, so the estimate errs towards stopping early for them; text in scripts that need more
tokens per character (Chinese, Japanese, Korean) can be underestimated (negligible: from the
second request on, the provider's own count replaces the estimate for everything but the new text).

**Undercount after a lost lease.** A turn that finishes after its worker lost the lease cannot be
recorded (the fence refuses the ``agent_runs`` update), so the job's cost across attempts may
undercount by that one turn per lost lease; the next holder does not guess it (ADR 0006 section 6,
accepted there). **And after a failed request:** a request that fails after the provider
started billing it (an error mid-stream, a timeout) returns no usage, so it isn't counted either.
The SDK also retries silently inside a turn (``max_retries=2``: a connection error or a timeout
before the response headers, 408, 409, 429, 5xx; never once the stream has started; the
provider's ``x-should-retry`` header can override that list for one answer, which leaves the
bound below unchanged), and only the
attempt that answers reports usage, so a billed earlier attempt is lost even when the turn
succeeds: at most two uncounted attempts per turn, three for the turn that ends a failed attempt
(same section).
"""

import math
from dataclasses import dataclass
from typing import Final

from classbeat.config import Settings

CHARS_PER_TOKEN: Final = 3


@dataclass(frozen=True, slots=True)
class Limits:
    max_turns: int = 12
    max_tool_calls: int = 40
    max_corrections: int = 3
    stage_seconds: float = 300.0
    job_cost_micros: int = 1_500_000
    turn_output_reserve: int = 8_000

    @classmethod
    def from_settings(cls, settings: Settings) -> "Limits":
        return cls(
            max_turns=settings.agent_max_turns,
            max_tool_calls=settings.agent_max_tool_calls,
            max_corrections=settings.agent_max_corrections,
            stage_seconds=settings.agent_stage_seconds,
            job_cost_micros=settings.agent_job_cost_micros,
            turn_output_reserve=settings.agent_turn_output_reserve,
        )


def tokens_for(chars: int) -> int:
    return math.ceil(max(chars, 0) / CHARS_PER_TOKEN)
