"""The preview clip's cache key (ADR 0042 section 6.4). Pure: no I/O.

The key is the SHA-256 of a canonical JSON (sorted keys, no spaces, UTF-8, no NaN) of:

- ``plan``: the SHA-256 of the **stored plan**, the reference-keyed render plan of
  ``output_body_policy.render_plan`` (never the child's plan, with its local paths and output);
- ``sources``: every source file's SHA-256, by reference;
- ``window``: the window in absolute class time ``[start_ms, end_ms)``;
- ``encoder``: the encoder settings;
- ``libsndfile`` and ``soxr``: the library versions;
- ``renderer``: ``render.RENDERER_VERSION`` (a test keeps it current).

The **session is not in the key**: the same window of the same audio is the same clip in every
session of its owner. Any audio edit changes the plan, so it invalidates every clip of the draft; a
workout-only edit keeps the plan, so its clips are hits (the accepted trade-off of section 6.4).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def plan_sha256(plan: Sequence[Mapping[str, Any]]) -> str:
    """The SHA-256 (hex) of the stored, reference-keyed render plan."""
    return hashlib.sha256(canonical([dict(part) for part in plan])).hexdigest()


@dataclass(frozen=True, slots=True)
class Identity:
    """What of the renderer itself goes into the key (``preview.identity`` reads it)."""

    encoder: Mapping[str, Any]
    libsndfile: str
    soxr: str
    renderer: int


@dataclass(frozen=True, slots=True)
class KeyInputs:
    plan_sha256: str
    sources: Mapping[str, str]  # reference -> the source file's SHA-256
    window: tuple[int, int]  # [start_ms, end_ms) in class time
    identity: Identity


def key_document(inputs: KeyInputs) -> dict[str, Any]:
    ident = inputs.identity
    return {
        "plan": inputs.plan_sha256,
        "sources": dict(sorted(inputs.sources.items())),
        "window": [int(inputs.window[0]), int(inputs.window[1])],
        "encoder": dict(ident.encoder),
        "libsndfile": ident.libsndfile,
        "soxr": ident.soxr,
        "renderer": int(ident.renderer),
    }


def clip_key(inputs: KeyInputs) -> str:
    """The clip's key (64 hex characters)."""
    return hashlib.sha256(canonical(key_document(inputs))).hexdigest()
