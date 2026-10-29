"""The job kinds (ADR 0005 section 2): each maps to its input model and its pipeline.

A kind is registered in a ``KindRegistry``; the worker claims only registered kinds. J2 ships no
real kind (``design_draft`` comes with D1); tests register a test-only kind.

``run(job_run)`` is the kind's pipeline for one attempt of the current phase. It reaches every
checkpoint through ``job_run`` (``classbeat.jobs.run``) and ends with an outcome whose ``store``
runs inside the final fenced transaction, so the stored result and the state change commit
together or not at all:

- ``Complete(store)``: the job ends ``completed`` (``store`` returns the revision id, or None);
- ``AwaitApproval(store)``: the job waits for the user (``store`` returns the ``song_list``
  revision id and its content hash). A job waits at most once; the approved phase sees
  ``job_run.approved`` and must end with ``Complete``.

It fails a run by raising ``JobFailure(category)``, also from inside ``store`` (a last check
in the final transaction): the store is rolled back and the attempt ends with that category.
"""

import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel

from classbeat.jobs.fence import FencedSession

if TYPE_CHECKING:
    from classbeat.jobs.run import JobRun

KIND_NAME = re.compile(r"^[a-z_]{1,40}$")  # the jobs.kind check of migration 0004
STAGE_NAME = KIND_NAME


@dataclass(frozen=True, slots=True)
class Complete:
    store: Callable[[FencedSession], Awaitable[UUID | None]] | None = None


@dataclass(frozen=True, slots=True)
class AwaitApproval:
    store: Callable[[FencedSession], Awaitable[tuple[UUID, bytes]]]


Outcome = Complete | AwaitApproval


@dataclass(frozen=True, slots=True)
class JobKind:
    name: str
    input_model: type[BaseModel]
    stages: tuple[str, ...]
    run: "Callable[[JobRun], Awaitable[Outcome]]"

    def __post_init__(self) -> None:
        if not KIND_NAME.match(self.name):
            raise ValueError("a kind name is 1 to 40 of a-z and _")
        if not self.stages or not all(STAGE_NAME.match(s) for s in self.stages):
            raise ValueError("a kind has at least one stage, each named like a kind")
        if self.input_model.model_config.get("extra") != "forbid":
            raise ValueError("a kind's input model must forbid extra fields")

    def parse_input(self, data: Mapping[str, Any]) -> BaseModel:
        return self.input_model.model_validate(dict(data))


class KindRegistry:
    def __init__(self) -> None:
        self._kinds: dict[str, JobKind] = {}

    def register(self, kind: JobKind) -> JobKind:
        if kind.name in self._kinds:
            raise ValueError("this kind is registered already")
        self._kinds[kind.name] = kind
        return kind

    def unregister(self, name: str) -> None:
        self._kinds.pop(name, None)

    def get(self, name: str) -> JobKind | None:
        return self._kinds.get(name)

    def names(self) -> list[str]:
        return sorted(self._kinds)

    def __iter__(self) -> Iterator[JobKind]:
        return iter(list(self._kinds.values()))


# The worker's registry. D1 registers ``design_draft`` here; M4 the song-picking kinds.
KINDS = KindRegistry()
