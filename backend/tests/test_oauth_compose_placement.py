"""Guard: OAuth client secrets in the dev compose.yaml (#521, review of #520).

The three ``oauth_<provider>_client_secret`` files are Docker secrets of web only (ADR 0003
section 5); no service gets a client secret from an environment variable, and the secrets are
the files ``secrets/<name>`` that ``scripts/worktree.sh init`` creates empty. Static checks over
the repository's own files, no network.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
GIT = shutil.which("git")
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


def test_no_environment_value_names_a_client_secret() -> None:
    """The variable's name can be harmless while its value pulls a client secret in
    (``X: ${OAUTH_GOOGLE_CLIENT_SECRET}`` or a path under ``/run/secrets/oauth_``): the keys-only
    check above misses that. The only secret path in a value is the postgres password file."""
    for service_name, service in _compose()["services"].items():
        for key, value in _env(service).items():
            text = str(value).lower()
            assert "client_secret" not in text, (service_name, key)
            assert "/run/secrets/oauth" not in text, (service_name, key)
            if "/run/secrets/" in text:
                assert (service_name, key, text) == (
                    "db",
                    "POSTGRES_PASSWORD_FILE",
                    "/run/secrets/postgres_password",
                ), (service_name, key)


def test_the_env_example_assigns_no_secret_variable() -> None:
    """``compose.yaml`` interpolates ``.env``, which init copies from ``.env.example``: a secret
    variable there would reach a value without being a file."""
    names = [
        line.split("=", 1)[0].strip()
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    ]
    assert names
    assert [n for n in names if "SECRET" in n.upper() or "PASSWORD" in n.upper()] == []


def _init(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    assert BASH is not None  # the `scratch` fixture skips without it
    return subprocess.run(  # noqa: S603 - the repository's own script, in a scratch repository
        [BASH, "scripts/worktree.sh", "init"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A throw-away git repository holding only the script and ``.env.example``: ``init`` runs
    for real in it (it asks git for the repository root), never in this checkout."""
    if BASH is None or GIT is None:
        pytest.skip("bash and git are needed to run worktree.sh init")
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "worktree.sh", tmp_path / "scripts" / "worktree.sh")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    subprocess.run([GIT, "init", "-q"], cwd=tmp_path, check=True, timeout=60)  # noqa: S603
    return tmp_path


def test_init_creates_the_three_files_empty_and_keeps_what_the_owner_put_there(
    scratch: Path,
) -> None:
    """Runs ``scripts/worktree.sh init``: every client secret file exists and is empty (that
    provider is off), the other secrets are not, and a second run never overwrites a file."""
    first = _init(scratch)
    assert first.returncode == 0, first.stderr
    secrets_dir = scratch / "secrets"
    for name in NAMES:
        assert (secrets_dir / name).is_file(), name
        assert (secrets_dir / name).read_bytes() == b"", name
        assert f"created secrets/{name} (empty placeholder)" in first.stdout
    assert (secrets_dir / "app_secret_key").stat().st_size >= 32
    assert (secrets_dir / "connection_kek").stat().st_size == 64
    for name in NAMES:  # the owner fills one in; init again must leave it
        (secrets_dir / name).write_text("owner-value", encoding="utf-8")
    second = _init(scratch)
    assert second.returncode == 0, second.stderr
    assert "created secrets/oauth" not in second.stdout
    for name in NAMES:
        assert (secrets_dir / name).read_text(encoding="utf-8") == "owner-value", name
    # Deleting one makes init recreate that one empty (the placeholder, never a random value).
    (secrets_dir / NAMES[0]).unlink()
    third = _init(scratch)
    assert third.returncode == 0, third.stderr
    assert (secrets_dir / NAMES[0]).read_bytes() == b""
