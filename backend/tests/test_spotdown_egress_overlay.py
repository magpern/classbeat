"""Guard: spotdown's route out is its own overlay (#1489; ADR 0040, 2026-11-24 amendment, item 7).

``compose.prod.spotdown.yaml`` keeps spotdown on internal networks and reads YouTube sourcing from
the owner's ``SPOTDOWN_YTDLP_SOURCING`` (default ``false``, never a literal ``true``); the outbound
network is only in ``compose.prod.spotdown-egress.yaml``, for ``spotdown-api`` and
``spotdown-worker`` only. Static checks over the repository's own files;
``scripts/check-prod-compose.sh`` checks the renders."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SWITCH = "${SPOTDOWN_YTDLP_SOURCING:-false}"
REQUIRED = "${SPOTDOWN_YTDLP_SOURCING:?"


def load(relative: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def networks(service: dict[str, Any]) -> list[str]:
    nets = service.get("networks") or []
    return sorted(nets if isinstance(nets, list) else nets.keys())


def test_the_spotdown_overlay_has_no_route_out_and_youtube_from_the_owner_switch() -> None:
    overlay = load("compose.prod.spotdown.yaml")
    assert all(n.get("internal") is True for n in overlay["networks"].values())
    assert "spotdown-egress" not in overlay["networks"]
    for name in ("spotdown-api", "spotdown-worker"):
        env = overlay["services"][name]["environment"]
        assert env["ALLOW_YTDLP_SOURCING"] == SWITCH, name  # never a literal true
        assert env["URL_ALLOWED_HOSTS"] == "", name  # the url source stays off
    for name, service in overlay["services"].items():
        assert "spotdown-egress" not in networks(service), name


def test_the_egress_overlay_gives_the_route_out_to_spotdown_api_and_worker_only() -> None:
    overlay = load("compose.prod.spotdown-egress.yaml")
    assert overlay["networks"] == {"spotdown-egress": {}}  # the project's own, not internal
    assert sorted(overlay["services"]) == ["spotdown-api", "spotdown-worker"]
    for name, service in overlay["services"].items():
        assert networks(service) == ["spotdown-egress"], name
        assert sorted(service) == ["environment", "networks"], name  # no port, secret or volume
        assert list(service["environment"]) == ["ALLOW_YTDLP_SOURCING"], name
        assert service["environment"]["ALLOW_YTDLP_SOURCING"].startswith(REQUIRED), name


def test_no_other_compose_file_names_the_route_out_or_the_switch() -> None:
    for path in sorted(ROOT.glob("compose*.y*ml")):
        if path.name in ("compose.prod.spotdown.yaml", "compose.prod.spotdown-egress.yaml"):
            continue
        content = path.read_text("utf-8")
        assert "spotdown-egress" not in content, path.name
        assert "SPOTDOWN_YTDLP_SOURCING" not in content, path.name
