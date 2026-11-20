"""Guard: spotdown's service key (ADR 0040 section 10) is a secret file on the **worker only**,
never an environment value, a build argument or an image file; ``scripts/worktree.sh init``
creates it empty (no key: spotdown off). Static checks over the repository's own files, no
network, no key.

The production compose files do not mount it yet: spotdown joins the production stack with its own
services in a later phase (ADR 0040 section 10, hosting), which adds the mount and its checks."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
NAME = "spotdown_service_key"


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def mounts(service: dict[str, Any]) -> list[str]:
    return [s if isinstance(s, str) else s["source"] for s in service.get("secrets", [])]


def test_the_dev_stack_mounts_the_key_on_the_worker_only() -> None:
    compose: dict[str, Any] = yaml.safe_load(text("compose.yaml"))
    holders = sorted(n for n, s in compose["services"].items() if NAME in mounts(s))
    assert holders == ["worker"]
    assert compose["secrets"][NAME] == {"file": f"./secrets/{NAME}"}


def test_no_other_compose_file_names_it_and_no_environment_holds_it() -> None:
    for path in sorted(ROOT.glob("compose*.y*ml")):
        content = path.read_text("utf-8")
        assert "CLASSBEAT_SPOTDOWN_SERVICE_KEY" not in content, path  # never a variable
        if path.name != "compose.yaml":
            assert NAME not in content, path.name


def test_the_key_is_in_no_image_or_workflow() -> None:
    for relative in ("Dockerfile", ".env.example"):
        assert NAME not in text(relative).lower()
        assert "SPOTDOWN_SERVICE_KEY" not in text(relative)
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        assert "SPOTDOWN_SERVICE_KEY" not in workflow.read_text("utf-8"), workflow.name


def test_init_creates_an_empty_placeholder() -> None:
    script = text("scripts/worktree.sh")
    names = script.split("SECRETS=(", 1)[1].split(")", 1)[0].split()
    assert NAME in names
    assert f'"$name" == {NAME}' in script
    assert re.search(r": > \"secrets/\$name\"", script)
