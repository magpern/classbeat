"""The keys of a studio output's external operations (ADR 0036 section 6, SR C7). Pure: no I/O.

A key is **bound to the approval, not to the job**: ``out:<class_id>:<revision_id>:<output>:
<attempt>:<step>``. A later job for the same approved draft finds the earlier row, so it cannot
make a second record; "Create another anyway" is the next ``attempt``, so its keys are new.

The steps and their outputs: ``create`` and ``publish`` (``swedebeat_playlist``), ``merge``
(``swedebeat_mix``), ``attach`` (``mowl``), ``spotify_create`` and ``spotify_fill``
(``spotify_playlist``). ``parse`` reads a key back only when it has exactly this shape (lower-case
UUIDs, an attempt from 1 to 1000, a step of its output), so a row of another kind or key is never
taken for an output's.
"""

import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from classbeat.studio.output_choice_policy import OUTPUTS

MAX_ATTEMPT: Final = 1000  # the class_outputs check
STEPS: Final = {
    "create": "swedebeat_playlist",
    "publish": "swedebeat_playlist",
    "merge": "swedebeat_mix",
    "attach": "mowl",
    "spotify_create": "spotify_playlist",
    "spotify_fill": "spotify_playlist",
}
_UUID: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_OUTPUT: Final = "|".join(OUTPUTS)
_STEP: Final = "|".join(STEPS)
KEY: Final = re.compile(rf"out:({_UUID}):({_UUID}):({_OUTPUT}):([1-9][0-9]{{0,3}}):({_STEP})")


@dataclass(frozen=True, slots=True)
class OutputKey:
    class_id: UUID
    revision_id: UUID
    output: str
    attempt: int
    step: str

    def __str__(self) -> str:
        return key(self.class_id, self.revision_id, self.output, self.attempt, self.step)


def key(class_id: UUID, revision_id: UUID, output: str, attempt: int, step: str) -> str:
    """The key of one step; ``ValueError`` for a step of another output or a bad attempt."""
    if STEPS.get(step) != output or not 1 <= attempt <= MAX_ATTEMPT:
        raise ValueError("not a step of this output, or not an attempt")
    return f"out:{class_id}:{revision_id}:{output}:{attempt}:{step}"


def parse(value: str) -> OutputKey | None:
    """The parts of an output's key, or ``None`` for anything else."""
    found = KEY.fullmatch(value) if isinstance(value, str) else None  # pyright: ignore[reportUnnecessaryIsInstance]
    if found is None:
        return None
    class_id, revision_id, output, attempt, step = found.groups()
    number = int(attempt)
    if STEPS[step] != output or number > MAX_ATTEMPT:
        return None
    return OutputKey(UUID(class_id), UUID(revision_id), output, number, step)
