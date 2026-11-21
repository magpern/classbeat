"""The in-process fake spotdown (contract v2, ADR 0040; SD-P4, #1301) over an httpx transport: no
socket, no network, deterministic. Public test values only.

The fake itself is ``spotdowncore`` (what it answers, ``fake.end``, ``fake.ends_by_source``,
``fake.documents``, ``fake.inject`` and the rest are described there); the e2e stack runs the same
code over HTTP (``e2e/fake-spotdown``, #1337), so the two cannot drift. This module is the pytest
side: the ``spotdown`` fixture, which puts the fake behind ``classbeat.spotdown.opener``, and the
names the tests import (re-exported from the core).
"""

import re
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from classbeat import egress_spotdown
from classbeat.spotdown import opener
from songanalysisdocs import youtube_doc
from spotdowncore import (
    CONTRACTS,
    ENDS,
    MATCH_STATUSES,
    ORIGIN,
    SCOPE_A,
    SCOPE_B,
    SERVICE_KEY,
    FakeSpotdown,
    Seen,
    evidence_of,
    example,
    explicit_group,
    transport,
)
from spotifycore import mowl_bpm, track_id

__all__ = [
    "CONTRACTS",
    "ENDS",
    "MATCH_STATUSES",
    "ORIGIN",
    "SCOPE_A",
    "SCOPE_B",
    "SERVICE_KEY",
    "FakeSpotdown",
    "Seen",
    "evidence_of",
    "example",
    "explicit_group",
    "spotdown",
    "transport",
]


_NUMBER = re.compile(r"(\d{1,3})\D*$")  # the last number of a title: its catalogue number


class SongSpotdown(FakeSpotdown):
    """The core with a video per Spotify track and, unless the test set one, its document (the e2e
    fake's rule, #1337): the fake Spotify's titles end in the track's catalogue number, so the
    document's tempo is the fake MOWL's tempo of that track and its timing is ``approximate``.
    Every Spotify song pick runs on spotdown (#1354), so every pick test has this one by default."""

    def video(self, kind: str, n: int, track: dict[str, object]) -> str:
        found = _NUMBER.search(str(track.get("title", "")))
        index = int(found[1]) if found else n
        video = f"{kind}{index:08d}"
        tempo = float(mowl_bpm(track_id(index)))
        self.documents.setdefault(f"youtube:{video}", youtube_doc(video, tempo))
        return video

    def analysis(self, analysis_id: str) -> dict[str, object]:
        """The document aligned to the reference last asked for, unless the test set its own: a
        placeholder reference id (``youtube_doc``'s) becomes the asked one, as spotdown's would."""
        document = super().analysis(analysis_id)
        asked = self.references.get(self.analyses.get(analysis_id, (None, ""))[1])
        alignment = document.get("alignment")
        if asked and isinstance(alignment, dict) and alignment["reference"]["id"] == "0" * 32:
            alignment["reference"] = dict(asked)
            document["provenance"]["alignment"]["reference"] = dict(asked)  # pyright: ignore
        return document


@pytest.fixture
def spotdown(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSpotdown]:
    """The fake behind ``classbeat.spotdown.opener`` (the production builder, through
    ``spotdown_test_client``, over the fake transport), with spotdown installed for the test."""
    fake = SongSpotdown()
    key = SecretStr(SERVICE_KEY)

    def open_client(total_seconds: float) -> httpx.AsyncClient:
        return egress_spotdown.spotdown_test_client(
            ORIGIN, key, transport(fake), total_seconds=total_seconds
        )

    def open_upload_client() -> httpx.AsyncClient:
        return egress_spotdown.spotdown_test_client(ORIGIN, key, transport(fake), upload=True)

    egress_spotdown.install_spotdown(ORIGIN, key)
    monkeypatch.setattr(opener, "open_client", open_client)
    monkeypatch.setattr(opener, "open_upload_client", open_upload_client)
    try:
        yield fake
    finally:
        egress_spotdown.install_spotdown(None, None)
