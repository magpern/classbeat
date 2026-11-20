"""The facts read from an ``analysis-v1`` document's ``provenance`` (ADR 0040 section 6): the audio
identity, the source kind, the timing policy's ``Analysed`` and ``Alignment`` and the decoded
duration check. Pure: no I/O. A missing or malformed value reads as absent (or, for a present
but unreadable duration check, as ``MALFORMED``), never as an exception.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown.timing_inputs_model import Alignment, Analysed, PlayedKind

SOURCE_KINDS: Final = frozenset({"upload", "url", "youtube"})
YOUTUBE_ID: Final = re.compile(r"[A-Za-z0-9_-]{11}")  # as ``request_model``'s source_ref
MALFORMED: Final = "malformed"
_CHECK_RESULTS: Final = frozenset({"ok", "tolerable"})
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class AudioIdentity:
    sha256: str
    fingerprint: str | None
    codec: str | None
    sample_rate_hz: float | None
    channels: float | None
    file_duration_s: float | None
    content_duration_s: float | None


def sha256_of(value: object) -> str | None:
    """A lowercase hex sha256 digest, or ``None``."""
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


def audio_identity(doc: Mapping[str, object]) -> AudioIdentity | None:
    audio = ft.obj(doc, "provenance", "audio")
    sha = None if audio is None else sha256_of(audio.get("sha256"))
    if audio is None or sha is None:
        return None
    return AudioIdentity(
        sha,
        ft.word(audio.get("fingerprint")),
        ft.word(audio.get("codec")),
        ft.num(audio.get("sample_rate_hz"), 0.0),
        ft.num(audio.get("channels"), 0.0),
        ft.num(audio.get("file_duration_s"), 0.0),
        ft.num(audio.get("content_duration_s"), 0.0),
    )


def source_kind(doc: Mapping[str, object]) -> str | None:
    source = ft.obj(doc, "provenance", "source")
    kind = None if source is None else source.get("kind")
    return kind if isinstance(kind, str) and kind in SOURCE_KINDS else None


def analysed(
    doc: Mapping[str, object], uploaded_as: tuple[PlayedKind, str] | None
) -> Analysed | None:
    """The timing policy's view of what was analysed; ``uploaded_as`` is classbeat's own record of
    the item it uploaded (spotdown keeps no name for an upload)."""
    identity, kind = audio_identity(doc), source_kind(doc)
    if identity is None or kind is None:
        return None
    return Analysed(kind, identity.sha256, identity.fingerprint, uploaded_as)


def alignment(doc: Mapping[str, object]) -> Alignment | None:
    """``ok`` with the residual and offset of the top-level ``alignment``, ``failed`` from
    ``provenance.alignment``, or ``None`` when no alignment was asked for."""
    record = ft.obj(doc, "provenance", "alignment")
    if record is not None and record.get("result") == "failed":
        return Alignment("failed", None, None)
    top = ft.obj(doc, "alignment")
    if top is None:
        return None if record is None else Alignment("failed", None, None)
    return Alignment("ok", ft.num(top.get("residual_s"), 0.0), ft.num(top.get("offset_s")))


def decoded_duration_check(doc: Mapping[str, object]) -> str | None:
    """``ok`` or ``tolerable``; ``None`` when no check is recorded; ``MALFORMED`` for a check that
    is there but unreadable, which ``timing_class_policy`` refuses (``duration_rejected``)."""
    source = ft.obj(doc, "provenance", "source")
    if source is None or "decoded_duration_check" not in source:
        return None
    check = ft.obj(source, "decoded_duration_check")
    result = None if check is None else check.get("result")
    return result if isinstance(result, str) and result in _CHECK_RESULTS else MALFORMED


def youtube_ref(doc: Mapping[str, object]) -> str | None:
    """``provenance.source.ref`` of a ``youtube`` analysis when it is an 11-character video id."""
    source = ft.obj(doc, "provenance", "source")
    ref = None if source is None or source.get("kind") != "youtube" else source.get("ref")
    return ref if isinstance(ref, str) and YOUTUBE_ID.fullmatch(ref) else None
