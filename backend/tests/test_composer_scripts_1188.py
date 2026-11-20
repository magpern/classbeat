"""The composer's e2e model scripts ``composer_fill`` and ``composer_swap`` (#1188, for #1191's e2e
stack): every turn is tagged with the stage it belongs to (each stage group gets a provider of its
own) and the design turns are the ones ``composertools`` runs through the real pipeline; a fill runs
to its wait with the scripted design turns read from the file. Their pick and choice turns are for
the e2e's pin and attached playlist and run in ``test_composer_e2e_world.py``. No network, no
model."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from composertools import ComposerEnv, choose_turns, pool_turns, swap, swap_script

SCRIPTS = Path(__file__).parent / "agent_scripts"
STAGES = {"pick_pool", "choose_songs", "revise"}

pytestmark = pytest.mark.usefixtures("jenv")


def load(name: str) -> dict[str, Any]:
    return json.loads((SCRIPTS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["composer_fill", "composer_swap"])
def test_the_turns_are_tagged_with_their_stage(name: str) -> None:
    script = load(name)
    assert {t["only_in"] for t in script["turns"]} == STAGES
    assert [t["only_in"] for t in script["turns"]] == sorted(
        (t["only_in"] for t in script["turns"]), key=["pick_pool", "choose_songs", "revise"].index
    )


async def test_the_fill_scripts_design_turns_run_through_the_pipeline(cenv: ComposerEnv) -> None:
    design = [t for t in load("composer_fill")["turns"] if t["only_in"] == "revise"]
    script = {
        "model": "fake-model",
        "drafts": load("composer_fill")["drafts"],
        "turns": [*pool_turns(), *choose_turns(), *design],
    }
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    _, list_id, _ = await cenv.waiting(rider, class_id, script)
    assert cenv.content(list_id)["v"] == 3


async def test_the_swap_scripts_design_turns_run_through_the_pipeline(cenv: ComposerEnv) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    _, list_id, _ = await cenv.waiting(rider, class_id)
    made = await swap(rider, class_id, list_id)
    assert made.status_code == 201, made.text
    design = [t for t in load("composer_swap")["turns"] if t["only_in"] == "revise"]
    cenv.songenv.script = swap_script(design=design)
    row = await cenv.songenv.run(UUID(made.json()["job_id"]))
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
