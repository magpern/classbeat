"""The composer's e2e model scripts ``composer_fill`` and ``composer_swap`` (#1188, for #1191's e2e
stack): every turn is tagged with the stage it belongs to (each stage group gets a provider of its
own), the pick turns are ``e2e_pick``'s and ``e2e_change``'s, and the design turns are the ones
``composertools`` runs through the real pipeline; a fill runs to its wait with the scripted design
turns read from the file. No network, no model."""

import json
from pathlib import Path
from typing import Any

import pytest

from composertools import ComposerEnv, choose_turns, pool_turns

SCRIPTS = Path(__file__).parent / "agent_scripts"
STAGES = {"pick_pool", "choose_songs", "revise"}

pytestmark = pytest.mark.usefixtures("jenv")


def load(name: str) -> dict[str, Any]:
    return json.loads((SCRIPTS / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "pick"), [("composer_fill", "e2e_pick"), ("composer_swap", "e2e_change")]
)
def test_the_turns_are_tagged_and_the_pick_is_the_e2e_pick(name: str, pick: str) -> None:
    script = load(name)
    assert {t["only_in"] for t in script["turns"]} == STAGES
    picked = [t for t in script["turns"] if t["only_in"] != "revise"]
    earlier = [t for t in load(pick)["turns"] if t.get("only_in") != "interpret"]
    assert [t["calls"] for t in picked] == [t["calls"] for t in earlier]


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
