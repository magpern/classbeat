"""The base of every input model a user can submit through ``POST /api/jobs`` (ADR 0005 s. 2).

A kind's input names its class (``class_id``, an existing class of the user) **or** a ``title``
(the jobs API creates the class), never both. A kind that uses a provider connection lists the
provider in ``CONNECTIONS`` and has a field ``<provider>_connection_id``: the API refuses the job
unless that id equals ``ctx.connection(provider)``, and refuses an approval unless it still does
and ``classbeat.connections`` reports it ``connected``. ``design_draft`` (D1) is::

    class DesignDraftInput(JobInput):
        CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)
        brief: ...
        music: ...
        mowl_connection_id: UUID

Kinds whose input model is not a ``JobInput`` are not creatable through the API.

A **model job**'s input is a ``ModelJobInput``: it also carries ``route_key`` (ADR 0012 section
5), the key of the model routes its stage groups resolve. It is fixed when the job is created and
stored with the input, so every attempt, and the approved phase of a job that waited, uses the
same key; a browser may never send it (``SERVER_FIELDS``: the jobs API answers 422). Until M5
fills it from the user's plan at creation (#223) it is ``"default"``; an input stored before it
existed parses with that default.
"""

import json
import re
from typing import Annotated, Any, ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from classbeat.connections import PROVIDERS


def jsonb_size(value: Any) -> int:
    """The length of ``value`` as jsonb prints it (``", "`` and ``": "``), in UTF-8 bytes. The one
    measure for the stored input and for a brief's column limit; the database check stays the
    backstop."""
    return len(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

ROUTE_KEY: Final = re.compile(r"[a-z0-9_]{1,40}")
DEFAULT_ROUTE_KEY: Final = "default"
RouteKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]


class JobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    CONNECTIONS: ClassVar[tuple[str, ...]] = ()
    # Fields only the server sets, whatever the kind: the jobs API refuses a body naming one.
    SERVER_FIELDS: ClassVar[frozenset[str]] = frozenset()

    class_id: UUID | None = None
    title: Title | None = None

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        for provider in cls.CONNECTIONS:
            if provider not in PROVIDERS:
                raise TypeError("CONNECTIONS names an unknown provider")
            field = cls.model_fields.get(f"{provider}_connection_id")
            if field is None or field.annotation is not UUID:
                raise TypeError("each provider in CONNECTIONS needs <provider>_connection_id")

    @model_validator(mode="after")
    def _one_class(self) -> Self:
        if (self.class_id is None) == (self.title is None):
            raise ValueError("give exactly one of class_id and title")
        return self

    def connection_ids(self) -> dict[str, UUID]:
        """The connection id the job uses per provider of ``CONNECTIONS``."""
        return {p: getattr(self, f"{p}_connection_id") for p in self.CONNECTIONS}


class ModelJobInput(JobInput):
    """The input of a job that runs model stages: ``route_key`` (see the module)."""

    SERVER_FIELDS: ClassVar[frozenset[str]] = frozenset({"route_key"})

    route_key: RouteKey = DEFAULT_ROUTE_KEY
