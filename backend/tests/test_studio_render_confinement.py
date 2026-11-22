"""#1357 (T109, SR C1): the confinement of the render child, tested against it. The boundary
(``classbeat.design.sandbox``: system call filter, Landlock, non-dumpable parent) and the limits
the parent keeps (``studio.render``: memory, wall clock, process group) used to be tested through
the local analysis child (deleted in #1357); the render child is the one child process left, so
the same guarantees are pinned here. Generated data only, no network; the Linux-only checks run in
CI.
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from classbeat.design import sandbox
from classbeat.studio import render
from classbeat.studio.render import RenderFailed

# ruff: noqa: ASYNC251, S603

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")
linux_only = pytest.mark.skipif(not sandbox.LINUX, reason="the sandbox is Linux's (the image)")


def test_the_filter_program_denies_every_listed_call_and_other_architectures() -> None:
    for machine in ("x86_64", "aarch64"):
        program = sandbox.filter_program(machine)
        assert program[0] == (0x20, 0, 0, 4)  # load the architecture first
        assert program[2] == (0x06, 0, 0, 0x80000000)  # and kill on any other
        assert program[-1] == (0x06, 0, 0, 0x7FFF0000)  # allow the rest
        denied = {k for code, _, _, k in program if code == 0x15}
        _, numbers = sandbox._ARCHES[machine]  # pyright: ignore[reportPrivateUsage]
        assert {numbers[name] for name in sandbox.DENIED} <= denied
        assert len(sandbox.DENIED) == len(numbers)
    assert (0x35, 0, 1, 0x40000000) in sandbox.filter_program("x86_64")  # x32 calls
    with pytest.raises(sandbox.SandboxError):
        sandbox.filter_program("riscv64")


CONFINED_PROBE = """
import json, os, socket, sys
spec = __import__("importlib.util").util.spec_from_file_location("m", sys.argv[1])
m = __import__("importlib.util").util.module_from_spec(spec); spec.loader.exec_module(m)
work, secret = sys.argv[2], sys.argv[3]
sb = m.load(m.DESIGN, "sandbox")
out = {"syscalls": sb.deny_syscalls()}
out["files"] = sb.restrict_files(m.read_only_paths(), [work, "/dev/null"])
def denied(call):
    try:
        call()
    except PermissionError:
        return True
    return False
out["socket"] = denied(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
out["unix"] = denied(lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
out["socketpair"] = denied(lambda: socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM))
out["setsid"] = denied(os.setsid)
out["secret"] = denied(lambda: open(secret).read())
out["secrets_dir"] = denied(lambda: os.listdir(os.path.dirname(secret)))
open(os.path.join(work, "written"), "w").write("ok")
out["work"] = open(os.path.join(work, "written")).read() == "ok"
import soundfile
out["soundfile"] = True
print(json.dumps(out))
"""


@linux_only
def test_the_confined_process_has_no_socket_no_new_session_and_no_file_outside(
    tmp_path: Path,
) -> None:
    work = tmp_path / "mix-x"
    work.mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    secret = secrets / "connection_kek"
    secret.write_text("not a real key")
    done = subprocess.run(
        [sys.executable, "-I", "-c", CONFINED_PROBE, str(render.CHILD), str(work), str(secret)],
        env=render.child_environment(work),
        capture_output=True,
        timeout=60,
        check=True,
    )
    out = json.loads(done.stdout)
    assert out["syscalls"] is True
    assert out["socket"]
    assert out["unix"]
    assert out["socketpair"]  # #429: no AF_UNIX pair either
    assert out["setsid"]
    assert out["work"]
    assert out["soundfile"]
    if sandbox.landlock_abi() > 0:
        assert out["files"] is True
        assert out["secret"]
        assert out["secrets_dir"]
    else:  # a kernel without Landlock: recorded as accepted in T109
        assert out["files"] is False


@linux_only
def test_the_worker_is_non_dumpable_when_render_files_starts_a_child(tmp_path: Path) -> None:
    """``render_files`` itself makes the worker non-dumpable before it starts the child (so the
    child cannot read the worker's memory through ``/proc``): the stand-in ``argv`` is called
    while the child's command line is built, right before ``Popen``, and reports the flag."""
    probe = (
        "import asyncio, ctypes, sys\n"
        "from pathlib import Path\n"
        "from classbeat.studio import render\n"
        "libc = ctypes.CDLL(None)\n"
        "work = Path(sys.argv[1])\n"
        "(work / 't.mp3').write_bytes(b'x')\n"
        "rate = render.render_policy.RATE\n"
        "tracks = [{'path': str(work / 't.mp3'), 'src_start': 0, 'src_end': rate,"
        " 'dst_start': 0, 'fade_in': 0, 'fade_out': 0}]\n"
        "seen = []\n"
        "def argv(*_):\n"
        "    seen.append(libc.prctl(3, 0, 0, 0, 0))\n"
        "    return [sys.executable, '-c', 'raise SystemExit(3)']\n"
        "before = libc.prctl(3, 0, 0, 0, 0)\n"
        "try:\n"
        "    asyncio.run(render.render_files(work, tracks, argv=argv))\n"
        "except render.RenderFailed:\n"
        "    pass\n"
        "print(before, *seen)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path)], capture_output=True, timeout=60, check=True
    )
    assert done.stdout.split() == [b"1", b"0"]


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except OSError:
        return False


def _one_second_track(work: Path) -> list[dict[str, Any]]:
    path = work / "t.mp3"
    path.write_bytes(b"x")  # never opened: the stand-in child does not read it
    return [
        {
            "path": str(path),
            "src_start": 0,
            "src_end": render.render_policy.RATE,
            "dst_start": 0,
            "fade_in": 0,
            "fade_out": 0,
        }
    ]


@linux_only
async def test_a_process_the_child_started_is_killed_with_it(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild"
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(150)'],"
        " stdout=subprocess.DEVNULL)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    with pytest.raises(RenderFailed):
        await render.render_files(
            tmp_path,
            _one_second_track(tmp_path),
            timeout_seconds=2.0,
            argv=lambda *_: [sys.executable, "-c", code],
        )
    # The grandchild does not hold the child's stdout, so only the group kill ends the wait for it.
    assert time.monotonic() - started < 20
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid)


@pytest.mark.skipif(sys.platform == "win32", reason="a launcher process stands before python")
async def test_a_child_over_the_memory_limit_is_killed_by_the_parent(tmp_path: Path) -> None:
    hog = (
        "import time\n"
        "b = bytearray(600 * 1024 * 1024)\n"
        "for i in range(0, len(b), 4096): b[i] = 1\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    with pytest.raises(RenderFailed, match="memory"):
        await render.render_files(
            tmp_path,
            _one_second_track(tmp_path),
            max_rss=200 * 1024 * 1024,
            argv=lambda *_: [sys.executable, "-c", hog],
        )
    assert time.monotonic() - started < 30  # killed at the limit, not left to sleep


async def test_the_child_gets_a_minimal_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    code = (
        "import json, os\n"
        "open('env.json', 'w').write(json.dumps(sorted(os.environ)))\n"
        "raise SystemExit(3)\n"
    )
    with pytest.raises(RenderFailed):
        await render.render_files(
            tmp_path, _one_second_track(tmp_path), argv=lambda *_: [sys.executable, "-c", code]
        )
    seen = set(json.loads((tmp_path / "env.json").read_text()))  # what the child really got
    assert not any("SECRET" in k.upper() or "KEY" in k.upper() or "DATABASE" in k for k in seen)
    assert seen <= {
        "PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED", "LC_CTYPE",
    }  # fmt: skip
