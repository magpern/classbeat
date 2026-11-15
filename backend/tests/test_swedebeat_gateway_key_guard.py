"""Guard: classbeat holds one SwedeBeat-related secret, the gateway's client key, and nothing of
SwedeBeat's own (ADR 0035 sections 2 and 8; threat model T19, T104). It replaces the app-key guard
of ADR 0015 section 4. Static checks over the repository's own files, no network, no real key:

- the client key comes from a secret file only, sits on web and worker only in every compose file,
  is in no image, workflow or environment, and is redacted from every log (the setting's own
  behaviour is ``test_swedebeat_gateway_key_setting.py``'s);
- no tracked file is shaped like SwedeBeat's old ``{"key", "iv"}`` app key or named after it;
- ``swedebeat_app`` (the retired secret) is in no compose file, workflow, image or setting; the
  image takes no build secret or argument and no workflow hands Docker a secret; the production
  compose check keeps refusing a ``swedebeat_app`` secret;
- no SwedeBeat host is in ``classbeat``, nothing in it uses the library's SwedeBeat calls, and the
  done-when search of ADR 0035 section 8 finds nothing in the places it names.
"""

import ast
import io
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from classbeat.config import (
    OPTIONAL_SECRET_FILE_FIELDS,
    SECRET_FILE_FIELDS,
    SECRET_FILE_VARIABLES,
    Settings,
)
from classbeat.log import SECRET_KEYS, JsonFormatter, redact
from classbeat.swedebeat_gateway.key_policy import check_key

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src" / "classbeat"
NAME = "swedebeat_gateway_key"
E2E_KEY_FILE = "e2e/fake-gateway/client-key.txt"
OLD = "swedebeat_app"  # the retired app key's secret and setting name
OLD_SECRET = "SWEDEBEAT_APP"  # the owner's retired repository secret
# ADR 0035 section 8's done-when: none of these anywhere in these places.
DONE_WHEN = re.compile(r"swedebeat\.se|swedebeat_app|swedebeat\.login|match_track")
DONE_WHEN_PLACES = ("backend/src/", "e2e/", ".github/")
# The old app key's shape, in either order: a 32-character key and a 16-character iv.
_KEY_FIRST = r'"key"\s*:\s*"[^"\n]{32}"\s*,\s*"iv"\s*:\s*"[^"\n]{16}"'
_IV_FIRST = r'"iv"\s*:\s*"[^"\n]{16}"\s*,\s*"key"\s*:\s*"[^"\n]{32}"'
KEY_SHAPED = re.compile(f"{_KEY_FIRST}|{_IV_FIRST}")
BINARY = frozenset({".png", ".ico", ".woff2", ".whl", ".lock", ".zip", ".gz", ".mp3", ".jpg"})

pytestmark = pytest.mark.skipif(
    not (ROOT / "Dockerfile").is_file(), reason="not a full checkout (no Dockerfile)"
)


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def compose(relative: str) -> dict[str, Any]:
    return yaml.safe_load(text(relative))


def mounts(service: dict[str, Any]) -> list[str]:
    return [s if isinstance(s, str) else s["source"] for s in service.get("secrets", [])]


def tracked() -> list[str]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git is not available here")
    out = subprocess.run(  # noqa: S603 - fixed arguments
        [git, "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [name for name in out.decode().split("\0") if name]


def tracked_text(name: str) -> str | None:
    path = ROOT / name
    if path.suffix in BINARY:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def compose_files() -> list[str]:
    return sorted(p.name for p in ROOT.glob("compose*.y*ml"))


def workflows() -> list[Path]:
    found = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert found
    return found


# --- The client key: a file, on web and worker only ---------------------------------------------


def test_the_key_is_a_secret_file_setting_and_the_old_key_is_no_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert NAME in SECRET_FILE_FIELDS
    assert NAME in OPTIONAL_SECRET_FILE_FIELDS
    assert SECRET_FILE_VARIABLES == {NAME: "CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE"}
    assert not [f for f in Settings.model_fields if f.startswith(OLD)]
    assert OLD not in SECRET_FILE_FIELDS
    # A leftover file or variable of the old key sets nothing (and refuses nothing).
    (tmp_path / OLD).write_text('{"key": "x", "iv": "y"}', encoding="utf-8")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_APP", "x")
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_KEY", "fake-gateway-client-key-Zq4mR8")
    settings = Settings()
    assert settings.swedebeat_gateway_key is None  # never from the environment
    assert not hasattr(settings, OLD)


@pytest.mark.parametrize("file", ["compose.yaml", "compose.prod.yaml"])
def test_the_key_is_a_secret_file_mounted_on_web_and_worker_only(file: str) -> None:
    config = compose(file)
    holders = sorted(n for n, s in config["services"].items() if NAME in mounts(s))
    assert holders == ["web", "worker"]
    assert config["secrets"][NAME] == {"file": f"./secrets/{NAME}"}


def test_the_e2e_stack_mounts_the_public_test_key_on_no_other_service() -> None:
    config = compose("compose.e2e.yaml")
    assert config["secrets"][NAME] == {"file": f"./{E2E_KEY_FILE}"}
    # compose.yaml's web and worker mount it; nothing the e2e file adds may (the second and third
    # webs, the fakes), and the e2e web and worker add no second mount of it.
    assert [n for n, s in config["services"].items() if NAME in mounts(s)] == []


def test_the_e2e_key_is_a_public_test_value_that_passes_the_rule() -> None:
    key = text(E2E_KEY_FILE).strip()
    assert "e2e" in key  # marked; never the owner's key
    assert "test" in key
    assert check_key(SecretStr(key)) is not None


@pytest.mark.parametrize("file", ["compose.yaml", "compose.prod.yaml", "compose.e2e.yaml"])
def test_no_service_has_the_key_or_its_file_in_its_environment(file: str) -> None:
    e2e_key = text(E2E_KEY_FILE).strip()
    for name, service in compose(file)["services"].items():
        env = service.get("environment") or {}
        items = env.items() if isinstance(env, dict) else (e.split("=", 1) for e in env)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        for variable, value in items:  # pyright: ignore[reportUnknownVariableType]
            assert "GATEWAY_KEY" not in str(variable).upper(), (file, name, variable)
            assert e2e_key not in str(value), (file, name, variable)


def test_no_workflow_or_image_holds_the_key() -> None:
    for path in workflows():
        body = path.read_text(encoding="utf-8")
        assert "SB_CLIENT_API_KEY" not in body, path.name  # the owner's record, read by nothing
    dockerfile = text("Dockerfile")
    assert NAME not in dockerfile
    assert "SB_CLIENT_API_KEY" not in dockerfile
    assert "GATEWAY" not in dockerfile.upper()


def test_the_env_examples_never_hold_the_key() -> None:
    for example in (".env.example", "docs/runbooks/prod.env.example"):
        body = text(example)
        assert not re.search(r"^[^#]*GATEWAY_KEY", body, re.M), example


def test_the_init_script_creates_an_empty_placeholder_and_nothing_for_the_old_key() -> None:
    script = text("scripts/worktree.sh")
    names = script.split("SECRETS=(", 1)[1].split(")", 1)[0].split()
    assert NAME in names
    assert OLD not in names
    assert f'"$name" == {NAME}' in script
    assert OLD not in script


# --- The key and the bearer are redacted --------------------------------------------------------


def test_the_redaction_names_are_on_the_list() -> None:
    assert {"bearer", "x-api-key", NAME, "swedebeat_device_id", "swedebeat_token"} <= SECRET_KEYS


def test_the_key_and_the_bearer_never_reach_a_log_line() -> None:
    key, bearer = "fake-gateway-client-key-Zq4mR8", "gAAAABfakeBearerValue0123456789"
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.gateway.redaction")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning(
            "Authorization: Bearer %s x-api-key: %s {'X-API-Key': '%s'} {\"bearer\": \"%s\"} "
            "swedebeat_gateway_key=%s",
            bearer,
            key,
            key,
            bearer,
            key,
            extra={"bearer": bearer, "headers": {"X-API-Key": key}, NAME: key},
        )
    finally:
        logger.removeHandler(handler)
    line = out.getvalue()
    assert key not in line
    assert bearer not in line
    assert json.loads(line)["bearer"] == "[redacted]"
    assert key not in redact(f"X-API-Key={key}")


# --- Nothing of SwedeBeat's old app key -------------------------------------------------------


def test_no_tracked_file_is_shaped_like_or_named_after_the_old_app_key() -> None:
    names = tracked()
    assert [n for n in names if Path(n).name.startswith((OLD, f"_{OLD}"))] == []
    assert not [n for n in names if n.startswith("e2e/fake-swedebeat/")]  # the old fake and its key
    found = [n for n in names if (body := tracked_text(n)) is not None and KEY_SHAPED.search(body)]
    assert found == []


def test_the_key_shape_search_would_find_a_planted_key() -> None:
    planted = json.dumps({"key": "k" * 32, "iv": "i" * 16})
    assert KEY_SHAPED.search(planted)
    assert KEY_SHAPED.search(json.dumps({"iv": "i" * 16, "key": "k" * 32}))
    assert not KEY_SHAPED.search(json.dumps({"key": "k" * 31, "iv": "i" * 16}))


@pytest.mark.parametrize("file", ["compose.yaml", "compose.prod.yaml", "compose.e2e.yaml"])
def test_no_compose_file_declares_or_mounts_the_old_secret(file: str) -> None:
    assert set(compose_files()) >= {"compose.yaml", "compose.prod.yaml", "compose.e2e.yaml"}
    config = compose(file)
    assert OLD not in (config.get("secrets") or {})
    for name, service in config["services"].items():
        assert OLD not in mounts(service), (file, name)


def test_the_old_name_is_in_no_compose_workflow_image_or_env_file() -> None:
    places = [*compose_files(), "Dockerfile", ".env.example", "docs/runbooks/prod.env.example"]
    places += [str(p.relative_to(ROOT)) for p in workflows()]
    for place in places:
        body = text(place)
        assert OLD not in body, place
        assert OLD_SECRET not in body, place
    for ignore in (".gitignore", ".dockerignore"):
        assert OLD not in text(ignore), ignore
    # The secrets folder itself stays out of git and out of the build context.
    assert re.search(r"^secrets/$", text(".gitignore"), re.M)
    assert "**/secrets" in text(".dockerignore")


def test_the_image_takes_no_build_secret_or_argument() -> None:
    dockerfile = text("Dockerfile")
    assert "--mount=type=secret" not in dockerfile
    assert re.findall(r"^\s*ARG\b", dockerfile, re.M) == []  # no argument could carry a key


def test_no_workflow_hands_docker_a_secret_and_the_release_builds_without_a_cache() -> None:
    for path in workflows():
        body = path.read_text(encoding="utf-8")
        for option in ("--secret", "secret-envs", "secret-files", "build-args", "build-arg"):
            assert option not in body, (path.name, option)
    release = yaml.safe_load(text(".github/workflows/release.yml"))
    on = release.get("on", release.get(True))
    assert on == {"push": {"tags": ["v*.*.*"]}}  # never a pull request
    build = next(
        s for s in release["jobs"]["image"]["steps"] if "build-push-action" in s.get("uses", "")
    )
    assert "env" not in build
    assert "secrets." not in json.dumps(build["with"])
    assert "cache-to" not in build["with"]
    assert "cache-from" not in build["with"]


def test_the_production_compose_check_refuses_the_old_secret() -> None:
    rules = text("scripts/check-prod-compose-rules.bash")
    assert 'select(.source == "swedebeat_app")] | length == 0)' in rules
    assert '(.secrets // {} | has("swedebeat_app") | not)' in rules
    selftest = text("scripts/check-prod-compose-selftest-compose.bash")
    assert selftest.count('"$sb_app"') == 1  # only declared
    assert '"$sb_app | .services.web.secrets' in selftest  # declared and mounted on web


# --- No SwedeBeat host and none of the library's SwedeBeat calls ----------------------------


def test_no_swedebeat_host_is_named_anywhere_in_classbeat() -> None:
    found = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*")
        if path.is_file()
        and path.suffix not in BINARY
        and re.search(r"(?i)swedebeat\.se\b", path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert found == []


def test_classbeat_uses_only_clean_title_of_the_library_swedebeat_module() -> None:
    """``mowl.swedebeat`` signs in and searches SwedeBeat itself (``login``, ``match_track``);
    classbeat takes only its pure ``clean_title`` (ADR 0035 section 6)."""
    found: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            where = str(path.relative_to(SRC))
            if isinstance(node, ast.ImportFrom) and node.module == "mowl.swedebeat":
                found += [(where, a.name) for a in node.names if a.name != "clean_title"]
            elif isinstance(node, ast.ImportFrom) and node.module == "mowl":
                found += [(where, a.name) for a in node.names if a.name == "swedebeat"]
            elif isinstance(node, ast.Import):
                found += [
                    (where, a.name) for a in node.names if a.name.startswith("mowl.swedebeat")
                ]
    assert found == []


def test_the_done_when_search_finds_nothing() -> None:
    """ADR 0035 section 8: ``git grep -E 'swedebeat\\.se|swedebeat_app|swedebeat\\.login|
    match_track'`` finds nothing under backend/src, e2e, compose*.yaml, Dockerfile or .github (the
    guard tests that search for these strings live in backend/tests, outside it)."""
    places = [n for n in tracked() if n.startswith(DONE_WHEN_PLACES)]
    places += [n for n in tracked() if "/" not in n and re.fullmatch(r"compose.*\.ya?ml", n)]
    places += ["Dockerfile"]
    assert any(p.startswith("backend/src/") for p in places)
    found = [n for n in places if (body := tracked_text(n)) is not None and DONE_WHEN.search(body)]
    assert found == []
