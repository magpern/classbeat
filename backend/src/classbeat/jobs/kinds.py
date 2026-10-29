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

Two seams for the kinds of ADR 0007 (section 3, K1):

- ``consumes_credit`` (default true): a kind that is false creates its job with
  ``credit_state = 'none'``; the jobs API never calls the ledger for it (no ``reserve`` at
  creation, no ``allows`` at approval) and settle, which settles only ``reserved`` credits,
  ignores it. The per-user limit counts its jobs like any other.
- ``admit(db, ctx, parsed_input) -> Admitted | Refused`` (optional): ``POST /api/jobs`` runs it
  inside the create transaction, under the owner lock, after the idempotency lookup and the
  generic checks (connections, the class, one open job per class, the per-user limit) and before
  anything is inserted, in a savepoint that is rolled back when it refuses, so a refusal creates
  no job, no class and nothing ``admit`` wrote. ``Admitted.fields`` are server-resolved fields
  added to the stored input; they must be among the kind's ``server_fields``, which a browser
  may never send (the API answers 422). ``Admitted.after_insert`` runs once the job row exists,
  in the same transaction, with the new job's id. A kind without ``admit`` behaves as before.
"""

import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs.fence import FencedSession

if TYPE_CHECKING:
    from classbeat.jobs.run import JobRun

KIND_NAME = re.compile(r"^[a-z_]{1,40}$")  # the jobs.kind check of migration 0004
STAGE_NAME = KIND_NAME
REASON = KIND_NAME  # a refusal reason is a code, never text from the input


@dataclass(frozen=True, slots=True)
class Complete:
    store: Callable[[FencedSession], Awaitable[UUID | None]] | None = None


@dataclass(frozen=True, slots=True)
class AwaitApproval:
    store: Callable[[FencedSession], Awaitable[tuple[UUID, bytes]]]


Outcome = Complete | AwaitApproval


@dataclass(frozen=True, slots=True)
class Admitted:
    """``admit`` accepts: ``fields`` are added to the stored input (each one of the kind's
    ``server_fields``); ``after_insert(db, job_id)`` runs after the job row is inserted."""

    fields: Mapping[str, Any] = field(default_factory=dict[str, Any])
    after_insert: Callable[[AsyncSession, UUID], Awaitable[None]] | None = None


@dataclass(frozen=True, slots=True)
class Refused:
    """``admit`` refuses with a reason code (409 ``{reason}``, plus ``counts``: whole numbers
    only, such as a limit and how much of it is used)."""

    reason: str
    counts: Mapping[str, int] = field(default_factory=dict[str, int])

    def __post_init__(self) -> None:
        if not REASON.match(self.reason):
            raise ValueError("a refusal reason is 1 to 40 of a-z and _")
        for name, value in self.counts.items():
            if not REASON.match(name) or name in ("reason", "errors") or type(value) is not int:
                raise ValueError("a refusal's counts are named codes with whole numbers")


class Admit(Protocol):
    """A kind's create-time check (annotations only: a ``UserContext`` is never built here)."""

    def __call__(
        self, db: AsyncSession, ctx: UserContext, parsed: BaseModel, /
    ) -> Awaitable[Admitted | Refused]: ...


@dataclass(frozen=True, slots=True)
class JobKind:
    name: str
    input_model: type[BaseModel]
    stages: tuple[str, ...]
    run: "Callable[[JobRun], Awaitable[Outcome]]"
    consumes_credit: bool = True
    admit: Admit | None = None
    server_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not KIND_NAME.match(self.name):
            raise ValueError("a kind name is 1 to 40 of a-z and _")
        if not self.stages or not all(STAGE_NAME.match(s) for s in self.stages):
            raise ValueError("a kind has at least one stage, each named like a kind")
        if self.input_model.model_config.get("extra") != "forbid":
            raise ValueError("a kind's input model must forbid extra fields")
        if self.server_fields and self.admit is None:
            raise ValueError("only admit can set server fields")
        for name in self.server_fields:
            declared = self.input_model.model_fields.get(name)
            if declared is None or declared.is_required():
                raise ValueError("a server field is an optional field of the input model")

    def parse_input(self, data: Mapping[str, Any]) -> BaseModel:
        return self.input_model.model_validate(dict(data))

    def admitted_input(self, parsed: BaseModel, fields: Mapping[str, Any]) -> BaseModel:
        """The input to store: ``parsed`` with ``admit``'s fields, validated by the input model
        again (the worker parses it with the same model). A field outside ``server_fields`` is
        a programming error, raised before anything is stored."""
        unknown = set(fields) - self.server_fields
        if unknown:
            raise ValueError("admit may only set the kind's server fields")
        return self.parse_input({**parsed.model_dump(mode="json"), **fields})


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
