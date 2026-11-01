"""#415, hardening A3 of ADR 0016 (T109): the analysis child's boundary
(``classbeat.design.sandbox``, ``mixchild``, ``mixanalysis``), MP3 decoding, the fixed block size
and the private temp root.
Generated audio only (never a recording), no network; the Linux-only checks run in CI.
"""

import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import soundfile
from mowl import music

from classbeat.design import mixanalysis, mixchild, mixfetch, sandbox
from classbeat.design.mixanalysis import CHILD, child_argv, child_environment
from classbeat.jobs import JobFailure, JobRun
from mediafake import kick_wav_bytes

# ruff: noqa: ASYNC251, S603

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")
linux_only = pytest.mark.skipif(not sandbox.LINUX, reason="the sandbox is Linux's (the image)")


class FakeRun:
    async def checkpoint(self) -> None:
        return None

    @property
    def job(self) -> JobRun:
        return cast(JobRun, self)


async def analyse(audio: Path, **kwargs: Any) -> mixanalysis.Analysis:
    return await mixanalysis.analyse_audio(
        FakeRun().job, audio, map_version=music.MAP_VERSION, **kwargs
    )


def _syncsafe(n: int) -> bytes:
    return bytes((n >> s) & 0x7F for s in (21, 14, 7, 0))


def kick_mp3_bytes(seconds: float = 60.0, bpm: float = 124.0, rate: int = 44100) -> bytes:
    """A stereo MP3 of the generated kick track, behind an ID3v2.4 tag (title and padding) as real
    files carry one. Encoded here by the libsndfile the ``soundfile`` wheel bundles."""
    data, _ = soundfile.read(io.BytesIO(kick_wav_bytes(seconds, bpm, rate)), dtype="float32")
    out = io.BytesIO()
    soundfile.write(out, np.stack([data, data], axis=1) * 0.5, rate, format="MP3")
    title = b"\x03synthetic kick"
    frame = b"TIT2" + _syncsafe(len(title)) + b"\x00\x00" + title
    body = frame + bytes(1024)  # padding
    return b"ID3\x04\x00\x00" + _syncsafe(len(body)) + body + out.getvalue()


# --- MP3 -------------------------------------------------------------------------------------


def test_the_bundled_decoder_reads_mp3() -> None:
    assert "MP3" in soundfile.available_formats()


async def test_an_mp3_mix_is_decoded_and_analysed_by_the_confined_child(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    audio = tmp_path / "mix.audio"  # the name the fetch gives it: no extension to go by
    audio.write_bytes(kick_mp3_bytes(60.0, 124.0))
    with caplog.at_level(logging.INFO, logger="classbeat.design.mixanalysis"):
        result = await analyse(audio, max_seconds=360.0)
    (song,) = result.music_map["songs"]
    assert song["bpm"] == pytest.approx(124.0, abs=0.5)
    assert result.music_map["duration"] == pytest.approx(60.0, abs=1.0)
    (record,) = [r for r in caplog.records if r.getMessage() == "swedebeat analysis confined"]
    assert record.syscalls is sandbox.LINUX  # pyright: ignore[reportAttributeAccessIssue]
    assert record.files is (sandbox.landlock_abi() > 0)  # pyright: ignore[reportAttributeAccessIssue]


# --- decoding --------------------------------------------------------------------------------


class Recorder:
    """A ``soundfile.SoundFile`` stand-in that records the block size asked for."""

    sizes: list[int] = []  # noqa: RUF012
    header: dict[str, int] = {}  # noqa: RUF012

    def __init__(self, path: str) -> None:
        self._inner = REAL_SOUNDFILE(path)
        self.samplerate = Recorder.header.get("samplerate", self._inner.samplerate)
        self.channels = Recorder.header.get("channels", self._inner.channels)
        self.frames = self._inner.frames

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *_exc: object) -> None:
        self._inner.close()

    def blocks(self, **kwargs: Any) -> Any:
        Recorder.sizes.append(kwargs["blocksize"])
        return self._inner.blocks(**kwargs)


REAL_SOUNDFILE = soundfile.SoundFile


@pytest.mark.parametrize("rate", [8000, 11025, 44100, 96000])
def test_blocks_are_a_fixed_number_of_frames_whatever_the_header_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rate: int
) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(kick_wav_bytes(5.0, rate=rate))
    Recorder.sizes, Recorder.header = [], {}
    monkeypatch.setattr(soundfile, "SoundFile", Recorder)
    assert len(mixchild.decode(str(audio), 60.0, music.SR)) > 0
    assert Recorder.sizes == [mixchild.BLOCK_FRAMES]
    assert mixchild.BLOCK_FRAMES == 65536


@pytest.mark.parametrize(
    "header", [{"samplerate": 10**9}, {"samplerate": 0}, {"channels": 9}, {"channels": 0}]
)
def test_a_header_rate_or_channel_count_out_of_bounds_is_not_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, header: dict[str, int]
) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(kick_wav_bytes(5.0))
    Recorder.sizes, Recorder.header = [], header
    monkeypatch.setattr(soundfile, "SoundFile", Recorder)
    with pytest.raises(ValueError, match="not audio"):
        mixchild.decode(str(audio), 60.0, music.SR)
    assert Recorder.sizes == []  # not a block was read


# --- the child: by path, importing nothing of classbeat ----------------------------------------


def test_the_child_is_run_by_path_not_as_a_module(tmp_path: Path) -> None:
    argv = child_argv(tmp_path / "mix.audio", 60.0, 1000)
    assert argv[:3] == [sys.executable, "-I", str(CHILD)]
    assert "-m" not in argv
    assert CHILD.is_file()
    assert CHILD.name == "mixchild.py"


def test_the_child_imports_nothing_of_classbeat(tmp_path: Path) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(os.urandom(4096))  # not audio: the child imports the library, then fails
    (tmp_path / mixanalysis.NUMBA_CACHE).mkdir()
    done = subprocess.run(
        [sys.executable, "-I", "-X", "importtime", str(CHILD), str(audio), "60.0", str(2**31)],
        env=child_environment(tmp_path),
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == mixchild.EXIT_FAILED
    assert done.stdout == b""
    imported = [line.rsplit("|", 1)[-1].strip() for line in done.stderr.decode().splitlines()]
    assert "mowl.music" in imported  # it got that far
    assert not [name for name in imported if name.startswith(("classbeat", "sqlalchemy"))]


async def test_each_child_gets_a_fresh_numba_cache_of_its_own(tmp_path: Path) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    seen = tmp_path.parent / f"{tmp_path.name}-seen"
    plant = "import os; open(os.path.join(os.environ['NUMBA_CACHE_DIR'], 'planted'), 'w')"
    look = (
        f"import os; open({str(seen)!r}, 'w').write("
        "os.environ['NUMBA_CACHE_DIR'] + '|' + ','.join(os.listdir(os.environ['NUMBA_CACHE_DIR'])))"
    )
    for code in (plant, look):
        with pytest.raises(JobFailure):
            await analyse(
                audio, max_seconds=60.0, argv=lambda *_, c=code: [sys.executable, "-c", c]
            )
    cache, listing = seen.read_text().split("|")
    assert Path(cache) == tmp_path / mixanalysis.NUMBA_CACHE  # inside the private directory
    assert listing == ""  # the first child's file is gone


# --- the parent refuses an unconfined answer ----------------------------------------------------


def _answer(confined: object) -> str:
    good = {
        "version": music.MAP_VERSION,
        "duration": 60.0,
        "songs": [{"start": 0.0, "end": 60.0, "bpm": 124.0, "first_downbeat": 0.4}],
        "events": [],
    }
    body: dict[str, Any] = {"map": good, "peak_rss": None}
    if confined is not None:
        body["sandbox"] = confined
    return f"print({json.dumps(json.dumps(body))})"


@pytest.mark.parametrize(
    ("confined", "accepted"),
    [
        ({"syscalls": True, "files": False}, True),
        ({"syscalls": True, "files": True}, True),
        ({"syscalls": False, "files": True}, False),
        ({"syscalls": 1, "files": True}, False),
        ({}, False),
        ("yes", False),
        (None, False),
    ],
)
async def test_on_linux_an_answer_without_the_system_call_filter_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confined: object, accepted: bool
) -> None:
    monkeypatch.setattr(mixanalysis, "LINUX", True)
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    code = _answer(confined)
    run = analyse(audio, max_seconds=60.0, argv=lambda *_: [sys.executable, "-c", code])
    if accepted:
        assert (await run).music_map["songs"]
    else:
        with pytest.raises(JobFailure) as failed:
            await run
        assert failed.value.category == "analysis_failed"


# --- the sandbox itself (Linux) ------------------------------------------------------------------


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
sb = m.sibling("sandbox")
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
out["setsid"] = denied(os.setsid)
out["secret"] = denied(lambda: open(secret).read())
out["secrets_dir"] = denied(lambda: os.listdir(os.path.dirname(secret)))
open(os.path.join(work, "written"), "w").write("ok")
out["work"] = open(os.path.join(work, "written")).read() == "ok"
import numpy
out["numpy"] = True
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
        [sys.executable, "-I", "-c", CONFINED_PROBE, str(CHILD), str(work), str(secret)],
        env=child_environment(work),
        capture_output=True,
        timeout=60,
        check=True,
    )
    out = json.loads(done.stdout)
    assert out["syscalls"] is True
    assert out["socket"]
    assert out["unix"]
    assert out["setsid"]
    assert out["work"]
    assert out["numpy"]
    if sandbox.landlock_abi() > 0:
        assert out["files"] is True
        assert out["secret"]
        assert out["secrets_dir"]
    else:  # a kernel without Landlock: recorded as accepted in T109
        assert out["files"] is False


@linux_only
def test_the_worker_is_non_dumpable_once_it_has_started_a_child() -> None:
    probe = (
        "import ctypes, sys\n"
        "from classbeat.design import sandbox\n"
        "libc = ctypes.CDLL(None)\n"
        "before = libc.prctl(3, 0, 0, 0, 0)\n"
        "assert sandbox.make_undumpable() is True\n"
        "print(before, libc.prctl(3, 0, 0, 0, 0))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, timeout=60, check=True
    )
    assert done.stdout.split() == [b"1", b"0"]


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except OSError:
        return False


@linux_only
async def test_a_process_the_child_started_is_killed_with_it(tmp_path: Path) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    pidfile = tmp_path / "grandchild"
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(JobFailure):
        await analyse(
            audio,
            max_seconds=60.0,
            timeout_seconds=2.0,
            argv=lambda *_: [sys.executable, "-c", code],
        )
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid)


# --- the private temp root ----------------------------------------------------------------------


@pytest.fixture
def temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_the_work_root_is_created_private(temp: Path) -> None:
    root = mixfetch.work_root()
    assert root == temp / mixfetch.ROOT_NAME
    assert root.is_dir()
    if sys.platform != "win32":
        assert root.stat().st_mode & 0o777 == 0o700
    assert mixfetch.work_root() == root  # an existing one that is ours is used


def test_a_symlinked_work_root_is_refused_and_not_swept(temp: Path) -> None:
    elsewhere = temp / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / "mix-keep").mkdir()
    try:
        (temp / mixfetch.ROOT_NAME).symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create symlinks")
    with pytest.raises(mixfetch.UnsafeWorkRoot):
        mixfetch.work_root()
    with pytest.raises(mixfetch.UnsafeWorkRoot), mixfetch.private_directory():
        pass
    assert mixfetch.sweep_work_root() == 0
    assert (elsewhere / "mix-keep").is_dir()


def test_a_work_root_that_is_a_file_is_refused(temp: Path) -> None:
    (temp / mixfetch.ROOT_NAME).write_bytes(b"x")
    with pytest.raises(mixfetch.UnsafeWorkRoot):
        mixfetch.work_root()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_work_root_others_can_enter_is_refused(temp: Path) -> None:
    root = temp / mixfetch.ROOT_NAME
    root.mkdir()
    root.chmod(0o755)
    with pytest.raises(mixfetch.UnsafeWorkRoot):
        mixfetch.work_root()
    (root / "mix-keep").mkdir()
    assert mixfetch.sweep_work_root() == 0
    root.chmod(0o700)
    assert mixfetch.work_root() == root
