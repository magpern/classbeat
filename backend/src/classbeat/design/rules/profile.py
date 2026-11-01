"""Rule profile model, loader, digest and ``select_profile`` (ADR 0017 sections 1 and 2).

A profile is a JSON file of numbers and ids from the closed enums in ``enums`` (no free text).
``profiles/index.json`` lists every profile with its version and the SHA-256 of its file, so a
change to a file without a version bump is visible (and a test fails); ``profiles/schema.json``
is the committed JSON Schema of ``Profile`` (a test keeps it in sync). Loading refuses anything
that does not validate. A missing profile is never an error: ``select_profile`` returns ``None``.
"""

import hashlib
import json
import math
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from classbeat.design.rules.enums import (
    MIN_N_PERCENTILE,
    RULE_SPECS,
    Basis,
    BriefOverride,
    ClassType,
    Evidence,
    Metric,
    RationaleId,
    RuleId,
    Severity,
    Sport,
)

PROFILES_DIR: Final = Path(__file__).resolve().parent.parent / "profiles"
SELECT_WITHIN_MINUTES: Final = 3
MAX_RULES: Final = 16
_SNAPSHOT = r"^\d{4}-\d{2}-\d{2}$"
_ID = r"^[a-z]+\.[a-z_]+\.[0-9]{1,3}$"
_FILE = r"^[a-z]+/[a-z_]+_[0-9]{1,3}\.json$"
_DIGEST = r"^[0-9a-f]{64}$"


class ProfileError(Exception):
    """A profile or the index is invalid, or the index does not match the files."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)


class ProfileRule(_Strict):
    rule: RuleId
    metric: Metric
    low: float | None
    high: float | None
    basis: Basis
    severity: Severity
    rationale_id: RationaleId
    brief_overrides: Annotated[list[BriefOverride], Field(max_length=7)]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        spec = RULE_SPECS[self.rule]
        if self.metric != spec.metric:
            raise ValueError(f"rule {self.rule} measures {spec.metric}, not {self.metric}")
        bound, other = (self.low, self.high) if spec.side == "low" else (self.high, self.low)
        if bound is None or other is not None:
            raise ValueError(f"rule {self.rule} sets exactly its {spec.side} bound")
        if self.basis == "p5" and spec.side != "low":
            raise ValueError("basis p5 is a low bound")
        if self.basis == "p95" and spec.side != "high":
            raise ValueError("basis p95 is a high bound")
        if self.basis == "range_10" and self.severity != "info":
            raise ValueError("basis range_10 forces severity info")
        if len(set(self.brief_overrides)) != len(self.brief_overrides):
            raise ValueError("brief_overrides must be unique")
        return self


class Profile(_Strict):
    schema_version: Annotated[Literal[1], Field(alias="schema")]
    id: Annotated[str, Field(pattern=_ID)]
    version: Annotated[int, Field(ge=1, le=10_000)]
    sport: Sport
    class_type: ClassType
    label_minutes: Annotated[int, Field(ge=5, le=180)]
    evidence: Evidence
    n: Annotated[int, Field(ge=1, le=100_000)]
    source: Literal["professional studio library"]
    snapshot: Annotated[str, Field(pattern=_SNAPSHOT)]
    rules: Annotated[list[ProfileRule], Field(max_length=MAX_RULES)]

    @field_validator("snapshot")
    @classmethod
    def _real_date(cls, v: str) -> str:
        date.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.id != f"{self.sport}.{self.class_type}.{self.label_minutes}":
            raise ValueError("id must be <sport>.<class_type>.<label_minutes>")
        if len({r.rule for r in self.rules}) != len(self.rules):
            raise ValueError("a rule id may appear once")
        if self.evidence == "weak" and self.rules:
            raise ValueError("weak evidence ships no rule")
        if self.evidence == "moderate" and any(r.severity != "info" for r in self.rules):
            raise ValueError("moderate evidence ships info rules only")
        if self.n < MIN_N_PERCENTILE and any(r.basis in ("p5", "p95") for r in self.rules):
            raise ValueError(f"a percentile basis needs n >= {MIN_N_PERCENTILE}")
        return self

    @property
    def file(self) -> str:
        return f"{self.sport}/{self.class_type}_{self.label_minutes}.json"


class IndexEntry(_Strict):
    file: Annotated[str, Field(pattern=_FILE)]
    version: Annotated[int, Field(ge=1, le=10_000)]
    digest: Annotated[str, Field(pattern=_DIGEST)]


class Index(_Strict):
    schema_version: Annotated[Literal[1], Field(alias="schema")]
    rules_version: Annotated[int, Field(ge=0, le=10_000)]
    profiles: dict[Annotated[str, Field(pattern=_ID)], IndexEntry]


def digest_of(raw: bytes) -> str:
    """SHA-256 of a profile file, line endings normalised so a checkout cannot change it."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def parse_profile(raw: bytes) -> Profile:
    try:
        return Profile.model_validate_json(raw)
    except ValidationError as exc:
        raise ProfileError(f"invalid profile: {exc}") from exc


class Registry:
    """Every shipped profile, validated and checked against ``index.json``."""

    def __init__(self, profiles: dict[str, Profile], digests: dict[str, str], rules_version: int):
        self.profiles = profiles
        self.digests = digests
        self.rules_version = rules_version

    def get(self, profile_id: str) -> Profile | None:
        return self.profiles.get(profile_id)

    def labels(self, sport: str, class_type: str) -> list[int]:
        return sorted(
            p.label_minutes
            for p in self.profiles.values()
            if p.sport == sport and p.class_type == class_type
        )


def load_registry(root: Path | None = None) -> Registry:
    """Load and verify all profiles under ``root`` (default: the packaged directory).

    Raises ``ProfileError`` for an invalid file, a file not in the index, an index entry whose
    file, version or digest differs from the file on disk, or a wrong ``rules_version``.
    """
    base = root or PROFILES_DIR
    try:
        index = Index.model_validate_json((base / "index.json").read_bytes())
    except (OSError, ValidationError) as exc:
        raise ProfileError(f"invalid index: {exc}") from exc
    profiles: dict[str, Profile] = {}
    digests: dict[str, str] = {}
    for pid, entry in index.profiles.items():
        try:
            raw = (base / entry.file).read_bytes()
        except OSError as exc:
            raise ProfileError(f"profile {pid}: cannot read {entry.file}") from exc
        profile = parse_profile(raw)
        if profile.id != pid or profile.file != entry.file:
            raise ProfileError(f"profile {pid}: id or file does not match the index")
        if profile.version != entry.version:
            raise ProfileError(f"profile {pid}: version differs from the index")
        if digest_of(raw) != entry.digest:
            raise ProfileError(f"profile {pid}: digest differs from the index (bump version)")
        profiles[pid] = profile
        digests[pid] = entry.digest
    listed = {e.file for e in index.profiles.values()}
    on_disk = {p.relative_to(base).as_posix() for p in base.glob("*/*.json")}
    if on_disk != listed:
        raise ProfileError("profile files and the index differ")
    expected = max((p.version for p in profiles.values()), default=0)
    if index.rules_version != expected:
        raise ProfileError("rules_version must be the maximum profile version")
    return Registry(profiles, digests, expected)


@lru_cache(maxsize=1)
def _default_registry() -> Registry:
    return load_registry()


def select_profile(
    class_type: str,
    length_min: float,
    sport: str = "cycling",
    *,
    registry: Registry | None = None,
) -> Profile | None:
    """The profile for an explicit class type and length, or ``None`` (never an error).

    The label nearest to ``length_min`` wins, within 3 minutes only; any ``(class_type, label)``
    without a file, any other sport and any non-finite or non-positive length gives ``None``.
    """
    if sport != "cycling" or not math.isfinite(length_min) or length_min <= 0:
        return None
    reg = registry or _default_registry()
    labels = reg.labels(sport, class_type)
    if not labels:
        return None
    nearest = min(labels, key=lambda lab: abs(lab - length_min))
    if abs(nearest - length_min) > SELECT_WITHIN_MINUTES:
        return None
    return reg.get(f"{sport}.{class_type}.{nearest}")


def schema_json() -> dict[str, Any]:
    """The JSON Schema of a profile (committed as ``profiles/schema.json``)."""
    return Profile.model_json_schema(by_alias=True)


def dumps_schema() -> str:
    return json.dumps(schema_json(), indent=2, sort_keys=True) + "\n"
