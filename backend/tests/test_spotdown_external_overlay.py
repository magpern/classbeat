"""Guard: classbeat never runs spotdown; it calls one at a configured URL (#1526; ADR 0040,
amendment of 2026-11-25). ``compose.prod.spotdown.yaml`` is the worker's side of the link only: the
URL, the key file and the external network. Static checks over the repository's own files;
``scripts/check-prod-compose.sh`` checks the renders and its self-tests show each rule can fail."""

import re
from pathlib import Path
from typing import Any

import yaml

from classbeat.spotdown.url_policy import SpotdownOrigin, spotdown_origin

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = "compose.prod.spotdown.yaml"
DEFAULT_URL = "http://spotdown-api:8000"


def load(relative: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def test_the_overlay_holds_the_workers_side_of_the_link_and_nothing_of_spotdown() -> None:
    overlay = load(OVERLAY)
    assert sorted(overlay) == ["networks", "secrets", "services"]  # no volumes, no x- anchors
    assert sorted(overlay["services"]) == ["worker"]
    worker = overlay["services"]["worker"]
    assert sorted(worker) == ["environment", "networks", "secrets"]
    assert worker["networks"] == ["spotdown"]
    assert worker["secrets"] == ["spotdown_service_key"]
    assert worker["environment"] == {
        "CLASSBEAT_SPOTDOWN_URL": "${SPOTDOWN_URL:-" + DEFAULT_URL + "}"
    }


def test_the_network_is_spotdowns_own_external_one_that_classbeat_never_creates() -> None:
    network = load(OVERLAY)["networks"]["spotdown"]
    assert network == {"external": True, "name": "spotdown"}


def test_the_default_url_passes_the_workers_url_policy() -> None:
    assert spotdown_origin(DEFAULT_URL) == SpotdownOrigin("http", "spotdown-api", 8000)


def test_no_production_file_or_deploy_script_runs_or_names_spotdowns_pieces() -> None:
    gone = re.compile(
        r"ghcr\.io/magpern/spotdown|spotdown-(redis|worker|egress|internal|data)|SERVICE_MODE|SERVE_UI"
        r"|SPOTDOWN_(VERSION|YTDLP_SOURCING|NETWORK|SERVICE_KEY_FILE)|SPOTDOWN_(API|WORKER)_(CPUS|MEMORY)"
    )
    files = [
        *ROOT.glob("compose.prod*.y*ml"),
        *ROOT.glob("scripts/*.sh"),
        *ROOT.glob("scripts/*.bash"),
    ]
    assert files
    # The checks name what they refuse; the install test's fixtures put the old names back.
    allowed = {
        "check-prod-compose-spotdown.bash",
        "check-prod-compose-selftest-spotdown.bash",
        "test-install-check.sh",
    }
    for path in files:
        if path.name in allowed:
            continue
        assert not gone.search(path.read_text("utf-8")), path.name
    assert not (ROOT / "compose.prod.spotdown-egress.yaml").exists()
