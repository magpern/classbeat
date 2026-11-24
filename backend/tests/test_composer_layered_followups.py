"""The swap, the change and the resume carry the class as it now is (#1510, item 16): after a
chat change of length or intensity the sketch has a stated layer, and the ``brief`` in the
inputs of the jobs that follow it is the base revision's layered brief (``stated_layer`` applied,
as ``song_fill._plan`` does), not the class's stored one, so the plan check in ``songs.admit``
judges the class as it now is. Never the network, a real account or a model."""

import json
import secrets
from typing import Any
from uuid import UUID

import pytest

from classbeat.jobs import JobFailure
from classbeat.songs import kind as song_kind
from composertools import ComposerEnv, swap
from songtools import Rider
from test_composer_change import send

pytestmark = pytest.mark.usefixtures("jenv")

LAYER: dict[str, Any] = {"length_min": 45, "intensity": "hard", "climbs": 1, "sprints": 2}


async def layered(cenv: ComposerEnv) -> tuple[Rider, UUID, UUID]:
    """A sketch a chat change of length and intensity left a stated layer on; the class's own
    brief (30 minutes, moderate) is the unlayered one."""
    rider = await cenv.songenv.rider()
    class_id, sketch = await cenv.sketched(rider)
    cenv.sql(
        "UPDATE class_revisions SET provenance = jsonb_set(provenance, '{stated}', "
        "CAST(:l AS jsonb)) WHERE id = :r",
        l=json.dumps(LAYER),
        r=sketch,
    )
    stored = cenv.sql("SELECT brief FROM classes WHERE id = :c", c=class_id).scalar_one()
    assert (stored["length_min"], stored["intensity"]) == (30, "moderate")
    return rider, class_id, sketch


def seen(cenv: ComposerEnv, job_id: UUID) -> tuple[int, str]:
    brief = cenv.songenv.row(job_id).input["brief"]
    return brief["length_min"], brief["intensity"]


async def test_a_swap_after_a_chat_change_of_length_carries_the_layered_brief(
    cenv: ComposerEnv,
) -> None:
    rider, class_id, _ = await layered(cenv)
    fill_id, list_id, _ = await cenv.waiting(rider, class_id)
    assert seen(cenv, fill_id) == (45, "hard")
    made = await swap(rider, class_id, list_id)
    assert made.status_code == 201, made.text
    assert seen(cenv, UUID(made.json()["job_id"])) == (45, "hard")


async def test_a_change_while_the_list_waits_carries_the_layered_brief(cenv: ComposerEnv) -> None:
    rider, class_id, _ = await layered(cenv)
    await cenv.waiting(rider, class_id)
    submitted = await send(rider, class_id)
    assert submitted.status_code == 201, submitted.text
    assert seen(cenv, UUID(submitted.json()["job_id"])) == (45, "hard")


async def test_a_resume_carries_the_layered_brief(
    cenv: ComposerEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, _ = await layered(cenv)
    job_id, list_id, digest = await cenv.waiting(rider, class_id)
    body = {"revision_id": str(list_id), "content_hash": digest, "save": True}
    assert (await rider.browser.post(f"/api/jobs/{job_id}/approve", body)).status_code == 200

    async def mismatch(*_args: object) -> object:
        raise JobFailure("playlist_mismatch")

    with monkeypatch.context() as patched:
        patched.setattr(song_kind, "composer_phase_two", mismatch)
        assert (await cenv.songenv.run(job_id)).error_category == "playlist_mismatch"
    key = {"Idempotency-Key": secrets.token_urlsafe(18)}
    made = await rider.browser.post(f"/api/classes/{class_id}/resume-songs", None, key)
    assert made.status_code == 201, made.text
    assert seen(cenv, UUID(made.json()["job_id"])) == (45, "hard")
