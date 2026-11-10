"""The small records inside a revision's provenance (ADR 0006 section 6; split out of
``classbeat.agent.records``, which re-exports them by import)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProvenanceUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)


class ProvenanceRules(BaseModel):
    """ADR 0017 section 2: the rule profile the model saw when it made the revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    profile: str = Field(pattern=r"^[a-z]+\.[a-z_]+\.[0-9]{1,3}$")
    version: int = Field(ge=1, le=10_000)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DuplicatedFrom(BaseModel):
    """ADR 0007 amendment of 2026-11-03 (#536): the revision a duplicate class's revision 1 was
    copied from, ids of the same user only. Read to name the source class, never followed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    class_id: UUID
    revision_id: UUID
    number: int = Field(ge=1)
    # The request that made the copy (D item 7): a retry with the same key is answered with this
    # class instead of a second one.
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
