"""#1390 (ADR 0042 sections 5 and 8): a confirmed cut pin whose music changed refuses the outputs
with ``pin_unconfirmed``, right after ``mix_not_applied``, at the choice and again when the job
runs; nothing is asked of the gateway. On the fakes (the SwedeBeat gateway, spotdown's ``exact``
maps, the render child stood in) against Postgres as ``classbeat_app``; never the network.

The edit overlay is E2's (#1391): here ``applied_form.overlay_pins`` stands in for the overlay a
draft would store, and the pinned draft is stored as E2's write would store it (an applied draft,
``origin: mix_choices``, with the pins in its spec). Without that overlay the same draft is not the
applied form of its proposal (``mix_not_applied``, fail closed).
"""

import copy
import json
from typing import Any
from uuid import UUID

import pytest

from classbeat.jobs.errors import RETRYABLE
from classbeat.studio.mix_choice_model import MixChoices, canonical
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.swedebeat_studio import applied_form
from classbeat.swedebeat_studio.applied_form import Form
from studiooutputs import (
    choice,
    outputs_on,  # noqa: F401 - fixture
    renders_fixture,  # noqa: F401 - the ``renders`` fixture
    rows,
    run_job,
)
from studiotools import StudioEnv
from test_studio_mix_not_applied import (
    applied_draft,
    asked_since,
    cut_last,
    designed_exact,
    post,
    recorded,
    refused,
)
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    spotdown,  # noqa: F401 - the fake spotdown the stand-in answers from
)

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default", "outputs_on")

DJ = json.loads(canonical(MixChoices.dj_defaults()))
MIX = ("swedebeat_playlist", "swedebeat_mix")


async def pinned_draft(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, *, changed: bool, overlay: bool = True
) -> tuple[Any, UUID, Any]:
    """The exact proposal with a confirmed exact pin on its first piece's end (at its own cut, so
    the composed total is the proposal's), stored as an applied draft with DJ mix's defaults; the
    pin confirmed against the recorded file, or another one (``changed``)."""
    rider, class_id, proposal = await designed_exact(studio, cut_last())
    pid = proposal.current_revision_id
    music = studio.songs.revision(pid).music
    spec = copy.deepcopy(music["mix_spec"])
    first = spec["tracks"][0]
    sha = {a["reference"]: a["sha256"] for a in music["analyses"]}[first["reference"]]
    against = ("0" if sha[0] != "0" else "1") * 64 if changed else sha
    pin = {"requested_sec": first["end_sec"], "mode": "exact", "confirmed": True,
           "confirmed_sec": first["end_sec"], "confirmed_against": against}  # fmt: skip
    first["end_pin"] = pin
    record = PinRecord.model_validate(
        {**pin, "reference": first["reference"], "occurrence": 0, "edge": "end"}
    )
    if overlay:
        monkeypatch.setattr(applied_form, "overlay_pins", lambda _found: (record,))
    current = applied_draft(studio, class_id, pid, spec, recorded(pid, DJ))
    return rider, class_id, current


async def test_a_pin_confirmed_against_the_recorded_file_is_admitted(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, current = await pinned_draft(studio, monkeypatch, changed=False)
    answer = await post(rider, class_id, choice(current, studio, *MIX))
    assert answer.status_code == 201, answer.text


@pytest.mark.parametrize(
    "outputs", [MIX, ("swedebeat_mix",), ("swedebeat_mix", "mowl"), ("spotify_playlist",)]
)
async def test_a_pin_whose_music_changed_is_refused_at_the_choice(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, outputs: tuple[str, ...]
) -> None:
    rider, class_id, current = await pinned_draft(studio, monkeypatch, changed=True)
    assert await refused(studio, rider, class_id, current, *outputs) == "pin_unconfirmed"


async def test_without_the_overlay_the_pinned_draft_is_not_applied_first(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mix_not_applied`` comes before ``pin_unconfirmed``; a pin with no record fails closed."""
    rider, class_id, current = await pinned_draft(studio, monkeypatch, changed=True, overlay=False)
    assert await refused(studio, rider, class_id, current, *MIX) == "mix_not_applied"


async def test_mix_not_chosen_comes_before_pin_unconfirmed(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, current = await pinned_draft(studio, monkeypatch, changed=True)
    assert await refused(studio, rider, class_id, current, "swedebeat_playlist") == (
        "mix_not_chosen"
    )


async def test_a_pin_whose_music_changed_is_refused_again_when_the_job_runs(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Created with the create-time facts stood in, the job's own check refuses it: no bearer,
    no download, no write, no attempt; not retried."""
    rider, class_id, current = await pinned_draft(studio, monkeypatch, changed=True)
    real = applied_form.form

    async def bypassed(*_args: Any) -> Form:
        return Form(mixed=True, applied_form=True, pin_unconfirmed=False)

    monkeypatch.setattr(applied_form, "form", bypassed)
    answer = await post(rider, class_id, choice(current, studio, *MIX))
    assert answer.status_code == 201, answer.text
    monkeypatch.setattr(applied_form, "form", real)  # the overlay stand-in stays
    before = len(studio.gateway.requests)
    done = await run_job(studio, UUID(answer.json()["job_id"]))
    assert (done.state, done.error_category) == ("failed", "pin_unconfirmed")
    assert asked_since(studio, before) == []
    assert rows(studio, rider.user_id) == []
    assert "pin_unconfirmed" not in RETRYABLE


async def test_the_create_transaction_checks_the_pin_again(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's pre-check passed (stood in); the create's own check (``admit``, under the class
    row's lock) still refuses: no job stored, nothing asked of the gateway."""
    rider, class_id, current = await pinned_draft(studio, monkeypatch, changed=True)
    real = applied_form.form
    calls: list[int] = []

    async def first_passes(*args: Any) -> Form:
        calls.append(1)
        return Form(True, True, False) if len(calls) == 1 else await real(*args)

    monkeypatch.setattr(applied_form, "form", first_passes)
    assert await refused(studio, rider, class_id, current, *MIX) == "pin_unconfirmed"
    assert len(calls) == 2  # the pre-check, then the create transaction
