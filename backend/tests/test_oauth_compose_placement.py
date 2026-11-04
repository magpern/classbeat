"""Guard: OAuth client secrets in the dev compose.yaml (#521, review of #520).

The three ``oauth_<provider>_client_secret`` files are Docker secrets of web only (ADR 0003
section 5); no service gets a client secret from an environment variable, and the secrets are
the files ``secrets/<name>`` that ``scripts/worktree.sh init`` creates empty. Static checks over
the repository's own files, no network.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
NAMES = [f"oauth_{p}_client_secret" for p in ("google", "github", "microsoft")]

pytestmark = pytest.mark.skipif(
    not (ROOT / "compose.yaml").is_file(), reason="not a full checkout (no compose.yaml)"
)


def _compose() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def _sources(service: dict[str, Any]) -> list[str]:
    return [s if isinstance(s, str) else s["source"] for s in service.get("secrets", [])]


def _env(service: dict[str, Any]) -> dict[str, Any]:
    env = service.get("environment") or {}
    if isinstance(env, list):  # "KEY=value" form
        return {e.split("=", 1)[0]: e.split("=", 1)[1] if "=" in e else "" for e in env}
    return env


@pytest.mark.parametrize("name", NAMES)
def test_only_web_holds_each_client_secret_file(name: str) -> None:
    services = _compose()["services"]
    assert [n for n, s in services.items() if name in _sources(s)] == ["web"]


@pytest.mark.parametrize("name", NAMES)
def test_each_client_secret_is_its_own_secrets_file(name: str) -> None:
    assert _compose()["secrets"][name] == {"file": f"./secrets/{name}"}


def test_no_service_has_a_client_secret_in_a_variable() -> None:
    for service_name, service in _compose()["services"].items():
        for key in _env(service):
            assert "SECRET" not in key.upper(), (service_name, key)
        assert "env_file" not in service, service_name


def test_init_creates_the_three_files_empty() -> None:
    script = (ROOT / "scripts/worktree.sh").read_text(encoding="utf-8")
    for name in NAMES:
        assert name in script
    assert '"$name" == oauth_*_client_secret' in script
