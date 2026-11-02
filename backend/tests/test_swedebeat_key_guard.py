"""Guard: Swedebeat's app key is in no git file, in no pull-request or CI image and in no log
(ADR 0015 section 4; threat model accepted risks). Static checks over the repository's own files,
no network, no key.

The key reaches exactly one place: the production image, built by the release workflow on a
version tag from the owner's repository secret ``SWEDEBEAT_APP`` and written by one
``RUN --mount=type=secret`` of the Dockerfile. These tests pin that shape, so a change that would
put the key in a pull-request build, a build argument, a layer cache, a log or git fails here.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SECRET = "SWEDEBEAT_APP"
FILE_NAME = "swedebeat_app"
# The one committed file shaped like the key: the public TEST key of the e2e fake.
TEST_KEY_FILE = "e2e/fake-swedebeat/app.json"

pytestmark = pytest.mark.skipif(
    not (ROOT / "Dockerfile").is_file(), reason="not a full checkout (no Dockerfile)"
)


def text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def tracked() -> list[str]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("git is not available here")
    out = subprocess.run(  # noqa: S603 - fixed arguments
        [git, "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [name for name in out.decode().split("\0") if name]


def test_no_tracked_file_is_or_holds_the_app_key() -> None:
    names = tracked()
    assert [n for n in names if Path(n).name.startswith(FILE_NAME)] == []
    key_shaped = re.compile(r'"key"\s*:\s*"[^"\n]{32}"\s*,\s*"iv"\s*:\s*"[^"\n]{16}"')
    found: list[str] = []
    for name in names:
        path = ROOT / name
        if path.suffix in {".png", ".ico", ".woff2", ".whl", ".lock", ".zip", ".gz"}:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if key_shaped.search(body):
            found.append(name)
    # Only the e2e fake's public TEST key, and this guard's own pattern, may look like it.
    assert [n for n in found if n != TEST_KEY_FILE] == []


def test_the_e2e_key_is_a_marked_test_value_and_is_not_swedebeats() -> None:
    data = json.loads(text(TEST_KEY_FILE))
    assert set(data) == {"key", "iv"}
    assert len(data["key"]) == 32
    assert len(data["iv"]) == 16
    assert "e2e-test" in data["key"]
    assert "e2e-test" in data["iv"]


def test_git_and_docker_ignore_a_key_file() -> None:
    assert re.search(r"^secrets/$", text(".gitignore"), re.M)
    assert re.search(r"^swedebeat_app\*$", text(".gitignore"), re.M)
    assert "**/secrets" in text(".dockerignore")
    assert "**/swedebeat_app*" in text(".dockerignore")


def test_the_dockerfile_takes_the_key_only_from_a_build_secret_mount() -> None:
    dockerfile = text("Dockerfile")
    assert len(re.findall(r"--mount=type=secret,id=swedebeat_app,required=false", dockerfile)) == 1
    for line in dockerfile.splitlines():
        code = line.split("#", 1)[0]
        if FILE_NAME not in code.lower() and SECRET.lower() not in code.lower():
            continue
        # The mount line, its continuation lines and the one REQUIRE_ ARG; no COPY, ADD or ENV.
        assert not re.match(r"\s*(COPY|ADD|ENV)\b", code), line
    assert re.search(r"^ARG REQUIRE_SWEDEBEAT_APP=0$", dockerfile, re.M)
    args = re.findall(r"^ARG\s+(\w+)", dockerfile, re.M)
    assert args == ["REQUIRE_SWEDEBEAT_APP"]  # the one flag; no argument could carry the key
    assert "/run/secrets/swedebeat_app /app/swedebeat_app.json" in dockerfile
    assert "install -m 0444" in dockerfile  # readable by the classbeat user, not writable


def test_only_the_release_workflow_has_the_secret_and_builds_without_a_cache() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflows
    for path in workflows:
        body = path.read_text(encoding="utf-8")
        if path.name == "release.yml":
            continue
        assert SECRET not in body, f"{path.name} mentions {SECRET}"
        assert "swedebeat_app" not in body or path.name == "ci.yml", path.name
    ci = text(".github/workflows/ci.yml")
    assert f"secrets.{SECRET}" not in ci
    assert "secret-envs" not in ci

    release = yaml.safe_load(text(".github/workflows/release.yml"))
    on = release.get("on", release.get(True))
    assert list(on) == ["push"]
    assert on["push"] == {"tags": ["v*.*.*"]}  # never a pull request
    raw = text(".github/workflows/release.yml")
    assert raw.count(f"secrets.{SECRET}") == 2  # the check step and the build step, nothing else
    jobs = release["jobs"]
    for name, job in jobs.items():
        for step in job["steps"]:
            holds = SECRET in json.dumps(step.get("env", {}))
            if name != "image":
                assert not holds, f"job {name} holds the secret"
    build = next(s for s in jobs["image"]["steps"] if "build-push-action" in s.get("uses", ""))
    assert build["with"]["secret-envs"] == "swedebeat_app=SWEDEBEAT_APP"
    assert build["with"]["build-args"] == "REQUIRE_SWEDEBEAT_APP=1"
    assert "cache-to" not in build["with"]  # a cache export would copy the key's layer
    assert "cache-from" not in build["with"]
    assert "secrets." not in json.dumps(build["with"])  # the value is never an input
    # Nothing prints it.
    for line in raw.splitlines():
        if "$SWEDEBEAT_APP" in line:
            assert not re.search(r"\b(echo|cat|tee)\b[^|]*\$SWEDEBEAT_APP", line), line
    check = next(s for s in jobs["image"]["steps"] if "Swedebeat" in s.get("name", ""))
    assert "exit 1" in check["run"]  # missing or malformed fails the release build


def test_pull_request_builds_pass_no_secret_to_docker() -> None:
    for path in (ROOT / ".github" / "workflows").glob("*.y*ml"):
        if path.name == "release.yml":
            continue
        body = path.read_text(encoding="utf-8")
        assert "--secret" not in body, path.name
        assert "secret-envs" not in body, path.name
        assert "secret-files" not in body, path.name


def test_the_runtime_secret_is_web_only_in_every_compose_file() -> None:
    for compose in ("compose.yaml", "compose.prod.yaml"):
        services = yaml.safe_load(text(compose))["services"]
        holders = [
            name
            for name, service in services.items()
            if "swedebeat_app"
            in [s if isinstance(s, str) else s["source"] for s in service.get("secrets", [])]
        ]
        assert holders == ["web"], compose
        assert "swedebeat_app" in yaml.safe_load(text(compose))["secrets"]
    e2e = yaml.safe_load(text("compose.e2e.yaml"))
    assert e2e["secrets"]["swedebeat_app"] == {"file": "./e2e/fake-swedebeat/app.json"}


def test_swedebeat_is_off_by_default_in_every_compose_file() -> None:
    for compose in ("compose.yaml", "compose.prod.yaml"):
        for service in ("web", "worker"):  # the worker checks a mix job's switch again (A4)
            env = yaml.safe_load(text(compose))["services"][service]["environment"]
            assert env["CLASSBEAT_SWEDEBEAT_ENABLED"] == "${SWEDEBEAT_ENABLED:-false}"
    assert "SWEDEBEAT_ENABLED=false" in text(".env.example")


def test_the_init_script_creates_an_empty_placeholder_never_a_key() -> None:
    script = text("scripts/worktree.sh")
    assert "swedebeat_app)" in script
    assert '"$name" == swedebeat_app' in script


def _key_step(tmp: Path, secret: Path, target: Path) -> str:
    """The Dockerfile's key step as a shell script, its two paths pointed into ``tmp``."""
    lines = text("Dockerfile").splitlines()
    start = next(
        i for i, line in enumerate(lines) if "--mount=type=secret,id=swedebeat_app" in line
    )
    block = [lines[start].split("RUN", 1)[1]]
    while block[-1].rstrip().endswith("\\"):
        start += 1
        block.append(lines[start])
    script = "\n".join(block)
    script = re.sub(r"^\s*--mount=\S+\s*\\n", "", script)
    script = script.replace("/run/secrets/swedebeat_app", secret.as_posix())
    return script.replace("/app/swedebeat_app.json", target.as_posix())


def _run_key_step(tmp_path: Path, secret: str | None, require: bool) -> tuple[int, Path]:
    sh = shutil.which("sh")
    if sh is None or shutil.which("python") is None:
        pytest.skip("sh and python are needed to run the Dockerfile's key step")
    secret_file = tmp_path / "secret"
    if secret is not None:
        secret_file.write_text(secret, encoding="utf-8")
    target = tmp_path / "swedebeat_app.json"
    env = {"REQUIRE_SWEDEBEAT_APP": "1" if require else "0", "PATH": os.environ["PATH"]}
    done = subprocess.run(  # noqa: S603 - fixed arguments, a throwaway script
        [sh, "-c", _key_step(tmp_path, secret_file, target)], env=env, capture_output=True
    )
    return done.returncode, target


def test_a_release_build_without_the_key_fails_and_an_ordinary_build_does_not(
    tmp_path: Path,
) -> None:
    # N2 of the #366 review: the Dockerfile itself must keep failing REQUIRE_SWEDEBEAT_APP=1
    # without a key; the static pins above would pass a step that no longer fails.
    good = json.dumps({"key": "k" * 32, "iv": "i" * 16})
    for missing in (None, ""):  # no secret mounted at all, and an empty one
        code, target = _run_key_step(tmp_path, missing, require=True)
        assert code != 0, repr(missing)
        assert not target.exists()
        code, target = _run_key_step(tmp_path, missing, require=False)
        assert code == 0, repr(missing)
        assert not target.exists()
    for bad in (
        "not json",
        json.dumps({"key": "short", "iv": "i" * 16}),
        json.dumps({"key": "k" * 32, "iv": "i" * 16, "x": 1}),
    ):
        for require in (True, False):
            code, target = _run_key_step(tmp_path, bad, require)
            assert code != 0
            assert not target.exists()
    code, target = _run_key_step(tmp_path, good, require=True)
    assert code == 0
    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(good)
