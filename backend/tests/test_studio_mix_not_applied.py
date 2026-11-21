"""#1370 (ADR 0036 amendment B9): nothing is mixed unless chosen, and only an applied form of its
proposal is written to SwedeBeat, at the choice and again when the job runs. On the fakes (the
SwedeBeat gateway, ``FakeProvider``, spotdown's ``exact`` maps, the render child stood in) against
Postgres as ``classbeat_app``; never the network.

- ``mix_not_applied``: an unapplied proposal with a cut on a track that is not ``exact``
  (``approximate``, or no timing decision), approved with DJ mix, is refused at the choice and,
  past it, when the job runs (no gateway request); the same proposal with only ``exact`` cuts
  inside the class length is admitted and produced; a proposal outside the class length is refused
  for the SwedeBeat playlist alone;
- an applied draft (``origin: mix_choices``, stored here as #1369's job will store it) is admitted
  only when its spec is ``applied`` of its recorded proposal and choices; another spec, another
  proposal id or choices that do not read are refused;
- ``mix_not_chosen``: a mixed draft for the SwedeBeat playlist alone is refused when the job runs
  too, and a draft that can no longer be shown to be whole songs (its recorded analysis replaced
  after the approval) cannot run; nothing is written either way.
"""

import copy
import json
import uuid
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import revision, track_analysis
from classbeat.jobs.errors import RETRYABLE
from classbeat.spotdown import studio_tracks_config
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal, canonical
from classbeat.studio.mix_choice_policy import applied
from classbeat.studio.mix_facts_model import of_map
from classbeat.swedebeat_studio import applied_form, outputs_admit
from classbeat.swedebeat_studio.applied_form import Form
from gatewaycore import SESSION_PATH
from songtools import new_key
from spotdownfake import spotdown  # noqa: F401 - fixture
from studiomaps import track_map
from studiooutputs import (
    Renders,
    choice,
    outputs_on,  # noqa: F401 - fixture
    produce,
    renders_fixture,  # noqa: F401 - the ``renders`` fixture
    rows,
    run_job,
)
from studiotools import StudioEnv, StudioRider
from test_spotdown_studio_design import ON, no_pause, spotdown_maps  # noqa: F401 - fixtures
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    MIX_8,
    WHOLE_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")


def cut_last() -> dict[str, Any]:
    """MIX_8 with the last song cut at 230 s (an exact track's cut is snapped to a bar)."""
    cut = copy.deepcopy(MIX_8)
    cut["tracks"][-1]["end_sec"] = 230.0
    return cut


def asked_since(studio: StudioEnv, before: int) -> list[str]:
    return [r.path for r in studio.gateway.requests[before:] if r.path != SESSION_PATH]


async def post(rider: StudioRider, class_id: UUID, body: dict[str, Any]) -> Any:
    path = f"/api/classes/{class_id}/outputs"
    return await rider.browser.post(path, body, {"Idempotency-Key": new_key()})


async def refused(
    studio: StudioEnv, rider: StudioRider, class_id: UUID, current: Any, *outputs: str
) -> str:
    """The reason the choice of ``outputs`` is refused; no job stored, nothing asked."""
    before = len(studio.gateway.requests)
    answer = await post(rider, class_id, choice(current, studio, *outputs))
    assert answer.status_code == 409, answer.text
    assert (
        studio.songs.sql("SELECT count(*) FROM jobs WHERE kind = 'produce_outputs'").scalar() == 0
    )
    assert asked_since(studio, before) == []
    return answer.json()["detail"]["reason"]


async def admitted_past_the_check(
    studio: StudioEnv,
    monkeypatch: pytest.MonkeyPatch,
    rider: StudioRider,
    class_id: UUID,
    current: Any,
    *outputs: str,
) -> Any:
    """The choice created with the create-time facts stood in (whole songs, the applied form), so
    the job-time check is the one tested; the job's row after it ran."""

    async def bypassed(*_args: Any) -> Form:
        return Form(mixed=False, applied_form=True)

    monkeypatch.setattr(applied_form, "form", bypassed)
    answer = await post(rider, class_id, choice(current, studio, *outputs))
    assert answer.status_code == 201, answer.text
    monkeypatch.undo()
    before = len(studio.gateway.requests)
    done = await run_job(studio, UUID(answer.json()["job_id"]))
    assert asked_since(studio, before) == []  # no bearer, no download, no write
    assert rows(studio, rider.user_id) == []  # no attempt opened
    return done


def set_timing(studio: StudioEnv, owner: UUID, reference: str, timing: str) -> None:
    """The stored timing class of one cached spotdown row; its map (and map hash) untouched."""
    studio.songs.sql(
        "UPDATE swedebeat_track_maps SET provenance = jsonb_set(provenance, '{timing,class}', "
        "to_jsonb(CAST(:t AS text))) WHERE owner_id = :o AND reference = :r "
        "AND analyser = 'spotdown'",
        o=owner,
        r=reference,
        t=timing,
    )


async def designed_exact(studio: StudioEnv, mix: dict[str, Any]) -> tuple[Any, UUID, Any]:
    studio_tracks_config.configure(ON)  # every track ``exact`` (spotdown's played recording)
    rider, class_id, _list, current = await designed(studio, mix)
    records = studio.songs.revision(current.current_revision_id).music["analyses"]
    assert {r["provenance"]["timing"]["class"] for r in records} == {"exact"}
    return rider, class_id, current


def last_reference(studio: StudioEnv, current: Any) -> str:
    spec = studio.songs.revision(current.current_revision_id).music["mix_spec"]
    return spec["tracks"][-1]["reference"]


# --- mix_not_applied on a proposal -----------------------------------------------------------


@pytest.mark.usefixtures("spotdown_maps")
async def test_a_cut_on_an_approximate_track_is_refused_with_dj_mix(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, current = await designed_exact(studio, cut_last())
    set_timing(studio, rider.user_id, last_reference(studio, current), "approximate")
    for outputs in (["swedebeat_mix"], ["swedebeat_playlist", "swedebeat_mix", "mowl"]):
        assert await refused(studio, rider, class_id, current, *outputs) == "mix_not_applied"
    assert await refused(studio, rider, class_id, current, "swedebeat_playlist") == (
        "mix_not_chosen"  # a cut is mixed: without DJ mix it is never chosen
    )
    done = await admitted_past_the_check(
        studio, monkeypatch, rider, class_id, current, "swedebeat_mix"
    )
    assert (done.state, done.error_category) == ("failed", "mix_not_applied")
    assert "mix_not_applied" not in RETRYABLE


@pytest.mark.parametrize(
    ("mix", "outputs", "reason"),
    [
        (None, ("swedebeat_mix",), "mix_not_applied"),  # the cut on a local track
        (MIX_8, ("swedebeat_playlist",), "mix_not_chosen"),
    ],
)
async def test_the_create_transaction_checks_both_facts_again(
    studio: StudioEnv,
    monkeypatch: pytest.MonkeyPatch,
    mix: dict[str, Any] | None,
    outputs: tuple[str, ...],
    reason: str,
) -> None:
    """The route's pre-check passed (stood in), the create's own check (``admit``, under the
    class row's lock) still refuses: no job stored, nothing asked of the gateway."""
    rider, class_id, _list, current = await designed(studio, mix or cut_last())
    real = applied_form.form
    calls: list[int] = []

    async def first_passes(*args: Any) -> Form:
        calls.append(1)
        return Form(False, True) if len(calls) == 1 else await real(*args)

    monkeypatch.setattr(applied_form, "form", first_passes)
    assert await refused(studio, rider, class_id, current, *outputs) == reason
    assert len(calls) == 2  # the pre-check, then the create transaction


async def test_a_cut_on_a_track_without_a_timing_decision_is_refused_with_dj_mix(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _list, current = await designed(studio, cut_last())  # local analyses
    assert await refused(studio, rider, class_id, current, "swedebeat_mix") == "mix_not_applied"
    done = await admitted_past_the_check(
        studio, monkeypatch, rider, class_id, current, "swedebeat_mix"
    )
    assert (done.state, done.error_category) == ("failed", "mix_not_applied")


@pytest.mark.usefixtures("spotdown_maps")
async def test_the_same_proposal_with_exact_cuts_is_admitted_and_produced(
    studio: StudioEnv, renders: Renders
) -> None:
    rider, class_id, current = await designed_exact(studio, cut_last())
    stored = studio.songs.revision(current.current_revision_id).music["mix_spec"]
    assert stored["tracks"][-1]["end_sec"] < 240.0  # the cut is kept (snapped): an exact track
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("completed", None)
    (plan,) = renders.plans
    assert len(plan) == 8


async def test_a_proposal_outside_the_class_length_is_refused_for_the_playlist(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    short = 180.0  # eight whole songs of 180 s: 1440 s, under 90 % of the list's 1800 s

    async def analyse_audio(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(music_map=track_map(short), peak_rss=None)

    monkeypatch.setattr(track_analysis, "analyse_audio", analyse_audio)
    whole = {
        "tracks": [{"track": n, "start_sec": 0, "end_sec": short} for n in range(8)],
        "crossfades": [0] * 7,
    }
    rider, class_id, list_id, current = await designed(studio, whole)
    stored = studio.songs.revision(current.current_revision_id).music["mix_spec"]
    target = studio.songs.revision(list_id).content["target_sec"]
    references = [t["reference"] for t in stored["tracks"]]
    facts = {r: of_map(track_map(short), None) for r in references}
    with pytest.raises(MixChoiceRefused) as caught:  # the reason is the class length (B6)
        applied(Proposal(references, stored, target), facts, MixChoices.whole_songs())
    assert caught.value.reason == "class_length_out_of_tolerance"
    assert await refused(studio, rider, class_id, current, "swedebeat_playlist") == (
        "mix_not_applied"
    )
    answer = await post(rider, class_id, choice(current, studio, "spotify_playlist"))
    assert answer.status_code == 201, answer.text  # a Spotify playlist alone, from any draft


async def test_a_plain_proposal_is_admitted_for_the_playlist_alone(studio: StudioEnv) -> None:
    rider, class_id, _list, current = await designed(studio, WHOLE_8)
    answer = await post(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert answer.status_code == 201, answer.text


@pytest.mark.parametrize("outputs", [["spotify_playlist"], ["swedebeat_playlist", "swedebeat_mix"]])
async def test_a_mixed_proposal_is_admitted_for_spotify_alone_and_with_dj_mix(
    studio: StudioEnv, outputs: list[str]
) -> None:
    rider, class_id, _list, current = await designed(studio)  # MIX_8: crossfades of 8 s
    answer = await post(rider, class_id, choice(current, studio, *outputs))
    assert answer.status_code == 201, answer.text


# --- an applied draft (origin: mix_choices) --------------------------------------------------


def applied_draft(
    studio: StudioEnv,
    class_id: UUID,
    proposal_id: UUID,
    spec: dict[str, Any],
    provenance: dict[str, Any],
) -> Any:
    """A derived draft as #1369's job stores it, made current: the proposal's content and
    analyses, ``spec`` and ``provenance``, the parent the proposal."""
    base = studio.songs.revision(proposal_id)
    music = {**base.music, "mix_spec": spec}
    made = uuid.uuid4()
    studio.songs.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
        "music, provenance, job_id, parent_id) SELECT :id, owner_id, class_id, "
        "(SELECT max(number) + 1 FROM class_revisions WHERE class_id = :c), 'draft', content, "
        ":hash, CAST(:music AS jsonb), CAST(:prov AS jsonb), job_id, id "
        "FROM class_revisions WHERE id = :p",
        id=made,
        c=class_id,
        p=proposal_id,
        hash=revision.studio_hash(base.content, spec),
        music=json.dumps(music),
        prov=json.dumps({**base.provenance, **provenance}),
    )
    studio.songs.sql(
        "UPDATE classes SET current_revision_id = :r WHERE id = :c", r=made, c=class_id
    )
    return studio.songs.sql(
        "SELECT current_revision_id FROM classes WHERE id = :c", c=class_id
    ).one()


def whole_spec(studio: StudioEnv, proposal_id: UUID) -> dict[str, Any]:
    """``applied`` of MIX_8's proposal with the whole-songs choices: every crossfade 0."""
    spec = copy.deepcopy(studio.songs.revision(proposal_id).music["mix_spec"])
    spec["crossfades"] = [0] * len(spec["crossfades"])
    return spec


def recorded(proposal_id: UUID, choices: object) -> dict[str, Any]:
    return {
        "origin": "mix_choices",
        "proposal_revision_id": str(proposal_id),
        "choices": choices,
    }


WHOLE_CHOICES = json.loads(canonical(MixChoices.whole_songs()))


async def test_an_applied_draft_equal_to_its_applied_form_is_admitted(studio: StudioEnv) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    pid = proposal.current_revision_id
    current = applied_draft(
        studio, class_id, pid, whole_spec(studio, pid), recorded(pid, WHOLE_CHOICES)
    )
    answer = await post(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert answer.status_code == 201, answer.text


async def test_an_applied_draft_whose_spec_is_not_its_applied_form_is_refused(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    pid = proposal.current_revision_id
    spec = whole_spec(studio, pid)
    spec["crossfades"][-1] = 8  # not what the recorded whole-songs choices give
    current = applied_draft(studio, class_id, pid, spec, recorded(pid, WHOLE_CHOICES))
    chosen = ("swedebeat_playlist", "swedebeat_mix")
    assert await refused(studio, rider, class_id, current, *chosen) == "mix_not_applied"
    done = await admitted_past_the_check(studio, monkeypatch, rider, class_id, current, *chosen)
    assert (done.state, done.error_category) == ("failed", "mix_not_applied")


@pytest.mark.parametrize(
    "provenance",
    [
        "another_id",  # not the proposal B4 resolves
        "no_id",
        "bad_choices",  # a position off the list
        "unknown_origin",  # neither a proposal nor an applied draft
    ],
)
async def test_an_applied_draft_with_another_record_is_refused(
    studio: StudioEnv, provenance: str
) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    pid = proposal.current_revision_id
    record: dict[str, Any] = {
        "another_id": recorded(uuid.uuid4(), WHOLE_CHOICES),
        "no_id": {"origin": "mix_choices", "choices": WHOLE_CHOICES},
        "bad_choices": recorded(pid, {**WHOLE_CHOICES, "dj_mix": True, "keep_whole": [99]}),
        "unknown_origin": {**recorded(pid, WHOLE_CHOICES), "origin": "duplicate"},
    }[provenance]
    current = applied_draft(studio, class_id, pid, whole_spec(studio, pid), record)
    assert await refused(studio, rider, class_id, current, "swedebeat_playlist") == (
        "mix_not_applied"
    )


# --- mix_not_chosen when the job runs ---------------------------------------------------------


async def test_a_mixed_draft_for_the_playlist_alone_is_refused_when_the_job_runs(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _list, current = await designed(studio)  # mixed, but the applied form
    assert await refused(studio, rider, class_id, current, "swedebeat_playlist") == (
        "mix_not_chosen"
    )
    done = await admitted_past_the_check(
        studio, monkeypatch, rider, class_id, current, "swedebeat_playlist"
    )
    assert (done.state, done.error_category) == ("failed", "mix_not_chosen")
    assert "mix_not_chosen" not in RETRYABLE


async def test_a_draft_no_longer_shown_whole_after_the_approval_cannot_run(
    studio: StudioEnv,
) -> None:
    rider, class_id, _list, current = await designed(studio, WHOLE_8)
    answer = await post(rider, class_id, choice(current, studio, "swedebeat_playlist"))
    assert answer.status_code == 201, answer.text
    # After the approval the recorded analyses are replaced (another map of the same tracks):
    # the stored spec can no longer be shown to be whole songs.
    studio.songs.sql(
        "UPDATE swedebeat_track_maps SET map = CAST(:m AS jsonb) WHERE owner_id = :o",
        o=rider.user_id,
        m=json.dumps(track_map(300.0)),
    )
    before = len(studio.gateway.requests)
    done = await run_job(studio, UUID(answer.json()["job_id"]))
    assert (done.state, done.error_category) == ("failed", "mix_not_chosen")
    assert asked_since(studio, before) == []
    assert rows(studio, rider.user_id) == []


def test_the_job_maps_each_refusal_to_its_own_failure() -> None:
    from classbeat.swedebeat_studio.outputs_kind import _AS_JOB  # pyright: ignore[reportPrivateUsage]

    assert _AS_JOB["mix_not_chosen"] == "mix_not_chosen"
    assert _AS_JOB["mix_not_applied"] == "mix_not_applied"
    assert outputs_admit.KIND == "produce_outputs"


# --- every swallowed error fails closed (AGENTS.md "PR loop", the class-wide self-check) --------


@pytest.mark.parametrize("error", [ValueError, KeyError, TypeError])
async def test_an_analysis_that_does_not_read_fails_closed(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    rider, class_id, _list, current = await designed(studio, WHOLE_8)  # admitted as it is

    def raising(*_args: Any) -> Any:
        raise error("x")

    monkeypatch.setattr(applied_form, "of_map", raising)
    assert await refused(studio, rider, class_id, current, "swedebeat_playlist") == (
        "mix_not_chosen"  # no lengths: not shown to be whole songs
    )
    assert await refused(studio, rider, class_id, current, "swedebeat_mix") == "mix_not_applied"


async def test_a_proposal_whose_music_does_not_read_fails_closed(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _list, proposal = await designed(studio)
    pid = proposal.current_revision_id
    current = applied_draft(
        studio, class_id, pid, whole_spec(studio, pid), recorded(pid, WHOLE_CHOICES)
    )

    class Unreadable:
        @staticmethod
        def model_validate_json(_raw: str) -> Any:
            raise ValueError("x")

    monkeypatch.setattr(applied_form, "StudioRevisionMusic", Unreadable)  # the proposal's read only
    assert await refused(studio, rider, class_id, current, "swedebeat_playlist") == (
        "mix_not_applied"
    )
