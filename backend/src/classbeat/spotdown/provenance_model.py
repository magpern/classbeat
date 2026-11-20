"""The provenance classbeat keeps per track in a class (ADR 0040 section 6, plan section 4a) and
the reuse rule. Pure: no I/O.

**Kept per track** (``TrackProvenance.record``): the analysis id; the analyser's name, version and
config hash; the audio identity (sha256, decoded-audio fingerprint, codec, sample rate, channels,
file and content durations); the source kind and, for ``youtube`` only, the video id; the timing
class with its reason and evidence; the versions of ``timing_class_policy``, ``track_fit_policy``
and ``spotdown_map_policy``. **Never:** a title, an artist, a file name, a URL, a ``cache_scope``,
a ``client_ref``, a token. Storing it needs a table (a migration), which is not part of this
module: the record is a plain JSON object for that later column, under ADR 0007 section 11.

The readers of the document's provenance are in ``provenance_read_calc``.

**Reuse** (``may_reuse``): a held analysis is reused only on an equal sha256 with the same
``analyzer_version``, or through a source-reference link; **never on the fingerprint alone**.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown import feature_trust_policy as ft
from classbeat.spotdown import spotdown_map_policy, timing_class_policy, track_fit_policy
from classbeat.spotdown.provenance_read_calc import (
    AudioIdentity,
    audio_identity,
    sha256_of,
    source_kind,
    youtube_ref,
)
from classbeat.spotdown.timing_class_policy import TimingDecision

RECORD_VERSION: Final = 1
POLICY_VERSIONS: Final = (
    ("spotdown_map_policy", spotdown_map_policy.POLICY_VERSION),
    ("timing_class_policy", timing_class_policy.POLICY_VERSION),
    ("track_fit_policy", track_fit_policy.POLICY_VERSION),
)


@dataclass(frozen=True, slots=True)
class TrackProvenance:
    analysis_id: str
    analyzer_name: str
    analyzer_version: str
    config_hash: str | None
    audio: AudioIdentity
    source_kind: str
    source_ref: str | None  # the YouTube video id; nothing for any other source
    decision: TimingDecision

    def record(self) -> dict[str, object]:
        """The JSON object stored with the track."""
        return {
            "record_version": RECORD_VERSION,
            "analysis_id": self.analysis_id,
            "analyzer": {
                "name": self.analyzer_name,
                "version": self.analyzer_version,
                "config_hash": self.config_hash,
            },
            "audio": {
                "sha256": self.audio.sha256,
                "fingerprint": self.audio.fingerprint,
                "codec": self.audio.codec,
                "sample_rate_hz": self.audio.sample_rate_hz,
                "channels": self.audio.channels,
                "file_duration_s": self.audio.file_duration_s,
                "content_duration_s": self.audio.content_duration_s,
            },
            "source": {"kind": self.source_kind, "ref": self.source_ref},
            "timing": {
                "class": self.decision.timing_class,
                "reason": self.decision.reason,
                "evidence": dict(self.decision.evidence),
            },
            "policy_versions": dict(POLICY_VERSIONS),
        }


def provenance_of(doc: Mapping[str, object], decision: TimingDecision) -> TrackProvenance | None:
    analyzer = ft.obj(doc, "provenance", "analyzer")
    identity, kind = audio_identity(doc), source_kind(doc)
    analysis_id = ft.word(doc.get("analysis_id"))
    if analyzer is None or identity is None or kind is None or analysis_id is None:
        return None
    name, version = ft.word(analyzer.get("name")), ft.word(analyzer.get("analyzer_version"))
    if name is None or version is None:
        return None
    ref = youtube_ref(doc) if kind == "youtube" else None
    config = sha256_of(analyzer.get("config_hash"))
    return TrackProvenance(analysis_id, name, version, config, identity, kind, ref, decision)


def may_reuse(
    held: AudioIdentity,
    held_version: str,
    *,
    sha256: str | None,
    analyzer_version: str,
    source_link: bool = False,
) -> bool:
    """Whether a held analysis may stand for the wanted audio: the same analyser version, and an
    equal sha256 or a source-reference link. An equal fingerprint is never enough."""
    if held_version != analyzer_version:
        return False
    return source_link or (sha256 is not None and sha256 == held.sha256)
