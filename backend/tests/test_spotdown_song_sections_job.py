"""SD-P6 stage D (#1303, ADR 0040 sections 2, 3 and 8; #1354): a ``design_with_songs`` job from the
pick to the first design with spotdown's analysis in the design, on the fakes (Spotify, the MOWL
pool, the model and ``spotdownfake``, in process; the netguard refuses any other connection).

- phase 1 labels the analysed songs ``timing approximate`` and phase 2 designs on spotdown's
  sections, every track stamped ``approximate``: the model sees ``"timing": "approximate"`` and the
  note, the design validator checks it, and the class completes;
- there is no fallback to MOWL's sections: spotdown unreachable or ``rate_limited`` at phase 2 is
  ``analysis_unavailable`` (the job is retried), an analysis that is gone is ``analysis_failed``, an
  error in the hook is ``internal``; in each the class is not completed and the design model is
  never sent a music step.
"""

import copy
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from classbeat.agent import track_timing_calc as tt
from classbeat.agent.timing_input import TIMING_NOTE
from classbeat.design.music import MusicStep
from classbeat.songs import phase2, phase2_spotdown
from classbeat.spotdown import outage_policy
from classbeat.spotdown.song_analysis_record_model import KEY
from classbeat.spotdown.song_sections_calc import reference_id
from songanalysisdocs import youtube_doc
from songtools import CHOSEN, Rider, SongEnv, song_id
from spotdownfake import FakeSpotdown, spotdown
from spotifycore import mowl_bpm
from test_spotdown_song_analysis_job import ending, vid

__all__ = ["spotdown"]  # the fixture

pytestmark = pytest.mark.usefixtures("songenv")


def docs(fake: FakeSpotdown) -> None:
    """Every catalogue song's YouTube source analyses as that song, aligned to its own track."""
    for n in range(20):
        doc = youtube_doc(vid(n), float(mowl_bpm(song_id(n))))
        doc["alignment"]["reference"]["id"] = reference_id(song_id(n))
        fake.documents[f"youtube:{vid(n)}"] = doc


@pytest.fixture
def hooked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, MusicStep, dict[str, Any]]]:
    """Each phase-2 hook call: today's analysis (as it came in) and the step it gave back."""
    calls: list[tuple[str, MusicStep, dict[str, Any]]] = []
    real = phase2.spotdown_music

    async def recording(run: Any, stored: Any, step: MusicStep) -> MusicStep:
        before = copy.deepcopy(dict(step.data.analysis))
        out = await real(run, stored, step)
        calls.append((str(run.lease.job_id), out, before))
        return out

    monkeypatch.setattr(phase2, "spotdown_music", recording)
    return calls


@pytest.fixture
def fake(spotdown: FakeSpotdown) -> FakeSpotdown:
    docs(spotdown)  # ``songenv`` already skips the polling waits
    return spotdown


def sent_to_design(songenv: SongEnv) -> str:
    """Everything the design model was sent in phase 2 (its stage spec and every tool result)."""
    design = songenv.providers[-1]
    parts: list[str] = []
    for s in design.sent:
        parts.append(f"{s.spec.system}\n{s.spec.first_message}")
        parts += [r.content for results, _ in s.turns for r in results]
    return "\n".join(parts)


async def designed(
    songenv: SongEnv, before_design: Callable[[], None] = lambda: None
) -> tuple[Any, Rider]:
    rider = await songenv.rider()
    job_id, revision_id = await songenv.approved(rider)
    before_design()
    row = await songenv.run(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    return songenv.revision(revision_id), rider


def reads(fake: FakeSpotdown) -> list[str]:
    return [s.path for s in fake.seen if s.method == "GET" and s.path.startswith("/v2/analyses/an")]


# --- the design ---------------------------------------------------------------------------------


async def test_the_design_gets_spotdowns_sections_stamped_approximate(
    songenv: SongEnv, fake: FakeSpotdown, hooked: list[Any]
) -> None:
    listed, _ = await designed(songenv)
    assert [s.get("timing") for s in listed.content["songs"]] == ["approximate"] * 8  # the label
    analysis_ids = [e["analysis_id"] for e in listed.provenance[KEY]["songs"]]
    phase_one = len(reads(fake)) - 8
    assert reads(fake)[phase_one:] == [f"/v2/analyses/{a}" for a in analysis_ids]  # one per song
    [(_, step, before)] = hooked
    m = step.data.analysis["map"]
    assert tt.classes(m) == {n: "approximate" for n in range(8)}
    assert [t["timing_source"] for t in m["tracks"]] == ["youtube"] * 8
    assert [t["spotify_id"] for t in m["tracks"]] == CHOSEN
    assert m["tracks"] == [
        {**t, "timing": "approximate", "timing_source": "youtube"} for t in before["map"]["tracks"]
    ]
    assert m["songs"] != before["map"]["songs"]  # spotdown's sections, not MOWL's
    text = sent_to_design(songenv)
    assert text.count('"sec":240.0,"timing":"approximate"}') >= 8  # each track in the summary
    assert json.dumps(TIMING_NOTE)[1:-1] in text  # the note, as JSON text


# --- no fallback ----------------------------------------------------------------------------------


def _unreachable(fake: FakeSpotdown, _mp: pytest.MonkeyPatch) -> None:
    fake.connect_error = True


def _status(code: int, **headers: str) -> Callable[[FakeSpotdown, pytest.MonkeyPatch], None]:
    def inject(fake: FakeSpotdown, _mp: pytest.MonkeyPatch) -> None:
        third = sorted(fake.analyses)[2]
        fake.inject[f"/v2/analyses/{third}"] = [httpx.Response(code, headers=headers)]

    return inject


def _hook_error(_fake: FakeSpotdown, mp: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise KeyError("injected")

    mp.setattr(phase2_spotdown, "apply_track", boom)


# (how spotdown fails at phase 2, the job's state after the attempt, its category)
PHASE_TWO = {
    "unreachable": (_unreachable, "queued", "analysis_unavailable"),
    "server_error": (_status(503), "queued", "analysis_unavailable"),
    "rate_limited": (_status(429, **{"Retry-After": "5"}), "queued", "analysis_unavailable"),
    "gone": (_status(404), "failed", "analysis_failed"),
    "hook_error": (_hook_error, "failed", "internal"),
}


LOGGED = {"unreachable": "connection", "server_error": "server_error"}  # the reason logged


@pytest.mark.parametrize("how", list(PHASE_TWO))
async def test_a_failure_at_phase_two_has_no_fallback_and_no_design(
    songenv: SongEnv,
    fake: FakeSpotdown,
    hooked: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    how: str,
) -> None:
    inject, state, category = PHASE_TWO[how]
    rider = await songenv.rider()
    job_id, revision_id = await songenv.approved(rider)
    inject(fake, monkeypatch)
    with caplog.at_level(logging.INFO):
        row = await songenv.run(job_id)
    assert (row.state, ending(songenv, job_id)) == (state, category)
    if how in LOGGED:
        assert outage_policy.log_message(LOGGED[how]) in caplog.text  # type: ignore[arg-type]
    assert hooked == []  # the hook never gave a step back: MOWL's sections were never used
    assert songenv.revision(revision_id).content.get("design") is None  # no design was made
    design = songenv.providers[-1]
    assert design.sent == [] or "approximate" not in sent_to_design(songenv)
