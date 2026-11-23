"""#1391 (ADR 0042 section 3): an edit overlay is stored only as a read would accept it
(``edit_overlay_model.storable``). An overlay a read would refuse that reached a write (here built
with ``model_construct``, which skips validation) is refused by the user-edit store
(``overlay_too_large``), and fails an Apply and a studio revise closed (``internal``); nothing is
stored. On the fakes (the SwedeBeat gateway, spotdown's ``exact`` maps, ``FakeProvider``) and
Postgres as ``classbeat_app``; never the network."""

from typing import Any
from uuid import UUID

import pytest

from classbeat.classes import user_edit_store
from classbeat.design.edit_overlay_model import EMPTY, EditOverlay, Override, of_records, storable
from classbeat.swedebeat_studio import revise_overlay
from classbeat.swedebeat_studio import studio_edit_calc as calc
from mixapplytools import NO_MODEL, WHOLE, create, current, designed, run_apply
from studiotools import StudioEnv
from test_edit_overlay_bounds import lock
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    design_script,
    spotdown,  # noqa: F401 - the fake spotdown the stand-in answers from
)
from test_user_edit_revise import CUT, edited, revised
from test_user_edit_routes import edit, revision_count

pytestmark = pytest.mark.usefixtures("spotdown_slot_by_default")

LOCK = {"op": "lock", "block": 1, "scope": "attributes"}  # no timing: Apply may move it


def past_bound(_records: Any = ()) -> EditOverlay:
    """An overlay of 201 records that skipped validation (the database's check refuses it too)."""
    return EditOverlay.model_construct(version=1, records=[lock(n) for n in range(201)])


def unreadable(_records: Any = ()) -> EditOverlay:
    """An overlay that skipped validation with a zone no draft could hold: within the database's
    count and byte check, so only ``storable`` refuses it."""
    zone = Override.model_construct(
        type="override", target=lock(0).target, field="zone", value=99, ai_value_at_set=3
    )
    return EditOverlay.model_construct(version=1, records=[zone])


def test_storable_is_the_stored_form_of_an_overlay_that_reads_back() -> None:
    assert storable(EMPTY) is None
    good = of_records([lock(0)])
    assert storable(good) == good.stored()
    with pytest.raises(ValueError, match="200"):
        storable(past_bound())
    with pytest.raises(ValueError, match="values a draft could"):
        storable(unreadable())


async def test_a_user_edit_with_an_unreadable_overlay_is_refused_and_stores_nothing(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, proposal = await designed(studio)
    count = revision_count(studio, class_id)
    monkeypatch.setattr(calc, "overlay_of", unreadable)
    answer = await edit(rider, class_id, proposal, LOCK)
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "overlay_too_large")
    assert (revision_count(studio, class_id), current(studio, class_id)) == (count, proposal)


async def test_an_apply_carrying_an_unreadable_overlay_fails_closed(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, proposal = await designed(studio)
    answer = await edit(rider, class_id, proposal, LOCK)
    assert answer.status_code == 200, answer.text
    base = UUID(answer.json()["revision_id"])
    count = revision_count(studio, class_id)
    studio.songs.script = NO_MODEL
    created = await create(studio, rider, class_id, WHOLE)
    assert created.status_code == 201, created.text
    monkeypatch.setattr(calc, "overlay_of", unreadable)  # the run's carried overlay
    row = await run_apply(studio, UUID(created.json()["id"]))
    assert (row.state, row.error_category) == ("failed", "internal")
    assert (revision_count(studio, class_id), current(studio, class_id)) == (count, base)


async def test_a_revise_reconciling_an_unreadable_overlay_fails_closed(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, base = await edited(studio, LOCK)
    count = revision_count(studio, class_id)
    studio.songs.script = design_script(CUT, note="Cut the last song.")
    monkeypatch.setattr(revise_overlay, "overlay_of", unreadable)
    row = await revised(studio, rider, class_id, base)
    assert (row.state, row.error_category) == ("failed", "internal")
    assert (revision_count(studio, class_id), current(studio, class_id)) == (count, base)


async def test_a_user_edit_past_the_content_bound_is_refused_and_stores_nothing(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, proposal = await designed(studio)
    count = revision_count(studio, class_id)
    monkeypatch.setattr(user_edit_store, "MAX_CONTENT_BYTES", 100)  # the 64 KB check, made small
    answer = await edit(rider, class_id, proposal, LOCK)
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (409, "invalid_edit")
    assert (revision_count(studio, class_id), current(studio, class_id)) == (count, proposal)
