"""Guard: spotdown's service key (ADR 0040 section 10) is a secret file on the **worker only**,
never an environment value, a build argument or an image file; ``scripts/worktree.sh init``
creates it empty (no key: spotdown off). Static checks over the repository's own files, no
network, no key.

In production only the spotdown overlay ``compose.prod.spotdown.yaml`` (#1481, off unless the owner
adds it to ``COMPOSE_FILE``) names it, on classbeat's worker only (spotdown runs elsewhere and holds
its own copy, #1526); never on web. ``compose.prod.yaml`` alone does not name it (no spotdown,
nothing changes)."""

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


E2E_KEY_FILE = "./e2e/fake-spotdown/service-key.txt"  # the e2e stack's PUBLIC test key (#1337)


def test_no_other_compose_file_names_it_and_no_environment_holds_it() -> None:
    for path in sorted(ROOT.glob("compose*.y*ml")):
        content = path.read_text("utf-8")
        assert "CLASSBEAT_SPOTDOWN_SERVICE_KEY" not in content, path  # never a variable
        if path.name not in ("compose.yaml", "compose.e2e.yaml", "compose.prod.spotdown.yaml"):
            assert NAME not in content, path.name


def test_the_production_overlay_mounts_the_key_on_the_worker_only() -> None:
    overlay: dict[str, Any] = yaml.safe_load(text("compose.prod.spotdown.yaml"))
    holders = sorted(n for n, s in overlay["services"].items() if NAME in mounts(s))
    assert holders == ["worker"]  # never web; classbeat runs no spotdown service (#1526)
    assert sorted(overlay["services"]) == ["worker"]
    assert overlay["secrets"][NAME] == {"file": f"./secrets/{NAME}"}  # classbeat's own copy
    worker_env = overlay["services"]["worker"]["environment"]
    assert worker_env == {"CLASSBEAT_SPOTDOWN_URL": "${SPOTDOWN_URL:-http://spotdown-api:8000}"}
    for name, service in overlay["services"].items():
        env = service.get("environment") or {}
        keys = [str(k).upper() for k in env]
        assert not any("SERVICE_KEY" in k for k in keys), name  # a path, never the key itself


def test_the_e2e_stack_mounts_its_public_test_key_on_the_worker_only() -> None:
    compose: dict[str, Any] = yaml.safe_load(text("compose.e2e.yaml"))
    holders = sorted(n for n, s in compose["services"].items() if NAME in mounts(s))
    assert holders == ["worker"]  # not web, the second and third webs, or the fake itself
    assert compose["secrets"][NAME] == {"file": E2E_KEY_FILE}
    for name, service in compose["services"].items():
        env = service.get("environment") or {}
        assert not any("SERVICE_KEY" in str(k).upper() for k in env), name


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
