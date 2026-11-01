"""A3 of ADR 0016 without a database: the media fetch (``classbeat.design.mixfetch``) against the
fake media endpoint with an injected list, the limited child process (``classbeat.design.
mixanalysis`` and ``mixchild``), the decode cap and the private directory. In-process fakes and
loopback only; the child runs the real library on generated audio.
"""

import io
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import numpy as np
import pytest
from mowl import music

from classbeat.design import mixanalysis, mixfetch
from classbeat.design.mixanalysis import (
    MAX_RSS_BYTES,
    child_environment,
    decode_limit_seconds,
    valid_map,
)
from classbeat.egress import Allowlist, mowl_media_test_client
from classbeat.jobs import JobFailure, JobRun, RunStopped
from classbeat.log import QUIET_LOGGERS, configure_logging
from mediafake import FakeMedia, kick_wav_bytes

# ruff: noqa: ASYNC240, ASYNC251, PT012, PT018, SIM117, SIM300

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


class FakeRun:
    """What the fetch and the analysis use of a ``JobRun``: its checkpoint."""

    def __init__(self) -> None:
        self.checkpoints = 0
        self.stop: Exception | None = None

    async def checkpoint(self) -> None:
        self.checkpoints += 1
        if self.stop is not None:
            raise self.stop

    @property
    def job(self) -> JobRun:
        return cast(JobRun, self)


def factory(media: FakeMedia, **kwargs: Any) -> Callable[[], httpx.AsyncClient]:
    return lambda: mowl_media_test_client(Allowlist(frozenset({FakeMedia.HOST})), media, **kwargs)


async def fetch(media: FakeMedia, link: str, directory: Path, **kwargs: Any) -> Path:
    run = FakeRun()
    return await mixfetch.fetch_mix_audio(
        run.job, link, directory, client_factory=factory(media), **kwargs
    )


# --- the fetch --------------------------------------------------------------------------------


async def test_a_good_link_is_written_to_the_private_directory(tmp_path: Path) -> None:
    media = FakeMedia()
    path = await fetch(media, media.link("ok", seconds=1.0), tmp_path)
    assert path.parent == tmp_path
    assert path.stat().st_size > 8000
    assert [m for m, _h, _p in media.requests] == ["GET"]
    (sent,) = media.headers
    assert set(sent) <= {"host"}  # none of our headers


async def test_the_content_type_is_not_trusted(tmp_path: Path) -> None:
    media = FakeMedia()
    for behaviour in ("ok", "octet"):
        path = await fetch(media, media.link(behaviour, seconds=1.0), tmp_path)
        assert path.stat().st_size > 0  # accepted either way; the decoder decides later


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "zstd", "gzip, identity"])
async def test_a_compressed_answer_is_refused_before_a_byte_is_read(
    tmp_path: Path, encoding: str
) -> None:
    media = FakeMedia()
    with pytest.raises(JobFailure) as refused:
        await fetch(media, media.link("gzip", encoding=encoding, seconds=1.0), tmp_path)
    assert refused.value.category == "media_refused"
    assert not (tmp_path / "mix.audio").exists()


async def test_a_compression_bomb_is_never_inflated(tmp_path: Path) -> None:
    media = FakeMedia()
    link = media.link("gzip", bomb=256 * 1024**2)  # 256 MiB of zeros in about 250 kB
    with pytest.raises(JobFailure) as refused:
        await fetch(media, link, tmp_path)
    assert refused.value.category == "media_refused"
    assert list(tmp_path.iterdir()) == []


def test_identity_is_the_only_content_coding_accepted() -> None:
    assert mixfetch._identity(httpx.Headers({}))
    assert mixfetch._identity(httpx.Headers({"content-encoding": "identity"}))
    assert mixfetch._identity(httpx.Headers({"content-encoding": " Identity "}))
    assert not mixfetch._identity(httpx.Headers({"content-encoding": "gzip"}))
    assert not mixfetch._identity(
        httpx.Headers([("content-encoding", "identity"), ("content-encoding", "br")])
    )


@pytest.mark.parametrize(
    ("behaviour", "options", "category"),
    [
        ("status", {"code": 403}, "media_refused"),
        ("status", {"code": 404}, "media_refused"),
        ("status", {"code": 410}, "media_refused"),
        ("status", {"code": 429}, "upstream_unavailable"),
        ("status", {"code": 500}, "upstream_unavailable"),
        ("status", {"code": 503}, "upstream_unavailable"),
        ("redirect", {}, "media_refused"),  # a same-host redirect is not followed
        ("redirect_away", {}, "media_host_not_allowed"),  # nor one to another host
        ("oversize", {}, "media_refused"),  # Content-Length over 250 MB: nothing is read
    ],
)
async def test_every_media_refusal_is_one_clean_category(
    tmp_path: Path, behaviour: str, options: dict[str, Any], category: str
) -> None:
    media = FakeMedia()
    link = media.link(behaviour, **options)
    with pytest.raises(JobFailure) as refused:
        await fetch(media, link, tmp_path)
    failure = refused.value
    assert failure.category == category
    assert failure.__cause__ is None
    assert failure.__suppress_context__ or failure.__context__ is None
    signature = media.signatures[-1]
    assert signature not in str(failure) and signature not in repr(failure.args)
    assert {host for _m, host, _p in media.requests} <= {FakeMedia.HOST}


async def test_the_streamed_count_is_held_to_the_cap_when_no_length_is_declared(
    tmp_path: Path,
) -> None:
    media = FakeMedia()
    link = media.link("oversize_stream", size=5 * (1 << 20))
    with pytest.raises(JobFailure) as refused:
        await fetch(media, link, tmp_path, max_bytes=1 << 20)
    assert refused.value.category == "media_refused"


async def test_a_declared_size_over_the_cap_is_refused_whatever_follows(tmp_path: Path) -> None:
    media = FakeMedia()
    link = media.link("oversize", size=mixfetch.MAX_BYTES + 1)
    with pytest.raises(JobFailure) as refused:
        await fetch(media, link, tmp_path)
    assert refused.value.category == "media_refused"
    assert mixfetch.MAX_BYTES == 250 * 1024 * 1024


async def test_a_slow_body_ends_at_the_total_deadline(tmp_path: Path) -> None:
    media = FakeMedia()
    run = FakeRun()
    started = time.monotonic()
    with pytest.raises(JobFailure) as refused:
        await mixfetch.fetch_mix_audio(
            run.job,
            media.link("slow", delay=0.05, chunks=1000),
            tmp_path,
            client_factory=factory(media, total_seconds=0.3),
        )
    assert refused.value.category == "upstream_unavailable"
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "link",
    [
        f"http://{FakeMedia.HOST}/media/x/mix.mp3",
        f"https://{FakeMedia.HOST}:8443/media/x/mix.mp3",
        f"https://user:pw@{FakeMedia.HOST}/media/x/mix.mp3",
        f"https://sub.{FakeMedia.HOST}/media/x/mix.mp3",
        "https://other.fake.test/media/x/mix.mp3",
        "https://169.254.169.254/latest/meta-data",
    ],
)
async def test_a_link_off_the_list_is_refused_and_nothing_is_sent(
    tmp_path: Path, link: str
) -> None:
    media = FakeMedia()
    with pytest.raises(JobFailure) as refused:
        await fetch(media, link, tmp_path)
    assert refused.value.category == "media_host_not_allowed"
    assert media.requests == []


@pytest.mark.parametrize("link", ["", "not a url", "x" * 5000, "https://", "ftp://x/y"])
async def test_a_link_that_is_not_a_url_is_refused_cleanly(tmp_path: Path, link: str) -> None:
    media = FakeMedia()
    with pytest.raises(JobFailure) as refused:
        await fetch(media, link, tmp_path)
    assert refused.value.category in {"media_refused", "media_host_not_allowed"}
    assert media.requests == []


async def test_the_production_client_with_its_empty_list_makes_no_request(
    tmp_path: Path,
) -> None:
    media = FakeMedia()
    run = FakeRun()
    with pytest.raises(JobFailure) as refused:
        await mixfetch.fetch_mix_audio(run.job, media.link("ok"), tmp_path)  # the real factory
    assert refused.value.category == "media_host_not_allowed"
    assert media.requests == []


async def test_a_stop_during_the_download_closes_it_and_leaves_the_file_to_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mixfetch, "CHECKPOINT_SECONDS", 0.05)
    media = FakeMedia()
    run = FakeRun()
    run.stop = RunStopped("cancelled")
    with mixfetch.private_directory(tmp_path) as directory:
        with pytest.raises(RunStopped):
            await mixfetch.fetch_mix_audio(
                run.job,
                media.link("slow", delay=0.02, chunks=500, chunk=1000),
                directory,
                client_factory=factory(media),
            )
        assert directory.exists()
    assert list(tmp_path.iterdir()) == []  # removed with its content on the way out


@pytest.fixture
def operator_log() -> Iterator[io.StringIO]:
    """The worker's real logging setup (``configure_logging``) at DEBUG, into a string."""
    root = logging.getLogger()
    names = (*QUIET_LOGGERS, "sqlalchemy.engine", "sqlalchemy.pool")
    saved = (list(root.handlers), root.level, {n: logging.getLogger(n).level for n in names})
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    try:
        yield stream
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        for name, level in saved[2].items():
            logging.getLogger(name).setLevel(level)


async def test_the_link_and_its_signature_are_never_logged(
    tmp_path: Path, operator_log: io.StringIO
) -> None:
    media = FakeMedia()
    await fetch(media, media.link("ok", seconds=1.0), tmp_path)
    for behaviour in ("status", "redirect_away", "gzip"):
        with pytest.raises(JobFailure):
            await fetch(media, media.link(behaviour), tmp_path)
    with pytest.raises(JobFailure):  # refused by the empty production list
        await mixfetch.fetch_mix_audio(FakeRun().job, media.link("ok"), tmp_path)
    text = operator_log.getvalue()
    for signature in media.signatures:
        assert signature not in text
    assert "sig=" not in text and "mix.mp3" not in text and "/media/" not in text
    assert FakeMedia.HOST in text  # the host alone is what the operator log names


def test_the_log_host_is_dns_characters_only() -> None:
    assert mixfetch.link_host("https://Media.Example.com/x?sig=abc") == "media.example.com"
    assert mixfetch.link_host("https://user:pw@media.example.com/x") == "media.example.com"
    assert mixfetch.link_host("not a url") == "invalid"
    assert mixfetch.link_host("https://exa mple.com/") == "invalid"


# --- the private directory ---------------------------------------------------------------------


def test_the_directory_is_private_and_removed_on_success_and_on_error(tmp_path: Path) -> None:
    with mixfetch.private_directory(tmp_path) as directory:
        (directory / "mix.audio").write_bytes(b"x")
        if os.name == "posix":
            assert directory.stat().st_mode & 0o777 == 0o700
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(RuntimeError):
        with mixfetch.private_directory(tmp_path) as directory:
            (directory / "mix.audio").write_bytes(b"x")
            raise RuntimeError
    assert list(tmp_path.iterdir()) == []


def test_the_sweep_at_worker_start_removes_what_a_crash_left(tmp_path: Path) -> None:
    for name in ("mix-aaaa", "mix-bbbb"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "mix.audio").write_bytes(b"x")
    (tmp_path / "numba-cache").mkdir()  # not audio: kept
    assert mixfetch.sweep_work_root(tmp_path) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["numba-cache"]
    assert mixfetch.sweep_work_root(tmp_path / "missing") == 0


# --- the decode cap ----------------------------------------------------------------------------


def test_the_decoded_length_is_the_listed_length_plus_five_minutes_never_over_120_minutes() -> None:
    assert decode_limit_seconds(0) == 300
    assert decode_limit_seconds(600) == 900
    assert decode_limit_seconds(119 * 60) == 7200
    assert decode_limit_seconds(7200) == 7200
    assert decode_limit_seconds(86_400) == 7200
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            decode_limit_seconds(bad)


# --- the child process --------------------------------------------------------------------------


def program(code: str) -> Callable[[Path, float, int], list[str]]:
    return lambda _audio, _seconds, _rss: [sys.executable, "-c", code]


async def analyse(audio: Path, run: FakeRun | None = None, **kwargs: Any) -> mixanalysis.Analysis:
    return await mixanalysis.analyse_audio(
        (run or FakeRun()).job, audio, map_version=music.MAP_VERSION, **kwargs
    )


async def test_the_fake_mix_is_analysed_in_a_child_within_the_memory_limit(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(kick_wav_bytes(60.0, 124.0))
    result = await analyse(audio, max_seconds=360.0)
    (song,) = result.music_map["songs"]
    assert song["bpm"] == pytest.approx(124.0, abs=0.5)
    assert result.music_map["duration"] == pytest.approx(60.0, abs=0.5)
    assert result.peak_rss is not None
    assert 0 < result.peak_rss < MAX_RSS_BYTES
    assert MAX_RSS_BYTES == int(1.5 * 1024**3)
    assert mixanalysis.ANALYSIS_SECONDS == 180.0


async def test_audio_longer_than_the_cap_ends_analysis_failed_before_any_analysis(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(kick_wav_bytes(400.0))  # a listed minute plus five: 360 s allowed
    with pytest.raises(JobFailure) as failed:
        await analyse(audio, max_seconds=decode_limit_seconds(60.0))
    assert failed.value.category == "analysis_failed"
    audio.write_bytes(kick_wav_bytes(300.0))  # the same cap lets 300 s through
    assert (await analyse(audio, max_seconds=decode_limit_seconds(60.0))).music_map["songs"]


async def test_the_length_is_counted_as_it_decodes_not_only_from_the_header(
    tmp_path: Path,
) -> None:
    # A header that lies low (or says nothing) cannot let a long file through: the blocks count.
    from classbeat.design import mixchild

    audio = tmp_path / "mix.audio"
    audio.write_bytes(kick_wav_bytes(60.0))

    import soundfile

    real = soundfile.SoundFile

    class LyingFile:
        def __init__(self, path: str) -> None:
            self._inner = real(path)
            self.samplerate = self._inner.samplerate
            self.channels = self._inner.channels
            self.frames = -1  # unknown, as for some streams

        def __enter__(self) -> "LyingFile":
            return self

        def __exit__(self, *_exc: object) -> None:
            self._inner.close()

        def blocks(self, **kwargs: Any) -> Any:
            return self._inner.blocks(**kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(soundfile, "SoundFile", LyingFile)
    try:
        with pytest.raises(ValueError, match="too long"):
            mixchild.decode(str(audio), 30.0, music.SR)
        assert len(mixchild.decode(str(audio), 90.0, music.SR)) > 0
    finally:
        monkey.undo()


async def test_a_file_the_decoder_cannot_open_ends_analysis_failed(tmp_path: Path) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(os.urandom(4096))
    with pytest.raises(JobFailure) as failed:
        await analyse(audio, max_seconds=300.0)
    assert failed.value.category == "analysis_failed"


async def test_audio_with_no_steady_beat_ends_analysis_failed(tmp_path: Path) -> None:
    from mediafake import _wav  # pyright: ignore[reportPrivateUsage]

    audio = tmp_path / "mix.audio"
    audio.write_bytes(_wav(np.zeros(11025 * 40), 11025))  # silence
    with pytest.raises(JobFailure) as failed:
        await analyse(audio, max_seconds=300.0)
    assert failed.value.category == "analysis_failed"


async def test_a_crash_of_the_child_ends_analysis_failed_and_the_parent_lives_on(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    for code in ("import os; os._exit(9)", "import os, signal; os.kill(os.getpid(), 9)"):
        if code.startswith("import os, signal") and os.name == "nt":
            continue
        with pytest.raises(JobFailure) as failed:
            await analyse(audio, max_seconds=60.0, argv=program(code))
        assert failed.value.category == "analysis_failed"


async def test_the_child_ends_itself_over_the_memory_limit(tmp_path: Path) -> None:
    # The real child with a limit its own import already passes: its watchdog thread ends it.
    audio = tmp_path / "mix.audio"
    audio.write_bytes(kick_wav_bytes(60.0))
    started = time.monotonic()
    with pytest.raises(JobFailure) as failed:
        await analyse(audio, max_seconds=360.0, max_rss=40 * 1024 * 1024)
    assert failed.value.category == "analysis_failed"
    assert time.monotonic() - started < 60


@pytest.mark.skipif(sys.platform == "win32", reason="a launcher process stands before python")
async def test_a_child_over_the_memory_limit_is_killed_by_the_parent(tmp_path: Path) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    hog = (
        "import time\n"
        "b = bytearray(600 * 1024 * 1024)\n"
        "for i in range(0, len(b), 4096): b[i] = 1\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    with pytest.raises(JobFailure) as failed:
        await analyse(audio, max_seconds=60.0, argv=program(hog), max_rss=200 * 1024 * 1024)
    assert failed.value.category == "analysis_failed"
    assert time.monotonic() - started < 30  # killed at the limit, not left to sleep


async def test_a_child_over_the_time_limit_is_killed(tmp_path: Path) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    started = time.monotonic()
    with pytest.raises(JobFailure) as failed:
        await analyse(
            audio,
            max_seconds=60.0,
            argv=program("import time; time.sleep(60)"),
            timeout_seconds=0.5,
        )
    assert failed.value.category == "analysis_failed"
    assert time.monotonic() - started < 20


async def test_a_cancel_kills_the_child_at_the_next_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mixanalysis, "CHECKPOINT_SECONDS", 0.2)
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    pidfile = tmp_path / "pid"
    code = f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    run = FakeRun()
    run.stop = RunStopped("cancelled")
    started = time.monotonic()
    with pytest.raises(RunStopped):
        await analyse(audio, run, max_seconds=60.0, argv=program(code))
    assert time.monotonic() - started < 20
    assert run.checkpoints >= 1
    from classbeat.design.rss import process_rss

    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while process_rss(pid) is not None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert process_rss(pid) is None  # the child is gone


@pytest.mark.parametrize(
    "code",
    [
        "print('not json')",
        "print('[1, 2]')",
        'print(\'{"map": {"version": 1}}\')',
        "import sys; sys.stdout.write('x' * (5 * 1024 * 1024))",
    ],
)
async def test_an_answer_that_is_not_a_valid_map_is_refused(tmp_path: Path, code: str) -> None:
    audio = tmp_path / "mix.audio"
    audio.write_bytes(b"x")
    with pytest.raises(JobFailure) as failed:
        await analyse(audio, max_seconds=60.0, argv=program(code))
    assert failed.value.category == "analysis_failed"


def test_the_child_gets_a_minimal_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    env = child_environment(tmp_path / "mix-x")
    assert not any("SECRET" in k.upper() or "KEY" in k.upper() for k in env)
    assert "secret" not in repr(env)
    assert set(env) <= {
        "PATH", "SYSTEMROOT", "NUMBA_CACHE_DIR", "NUMBA_NUM_THREADS", "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "TMPDIR", "TEMP", "TMP",
        "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    }  # fmt: skip


def good_map() -> dict[str, Any]:
    return {
        "version": music.MAP_VERSION,
        "duration": 60.0,
        "songs": [{"start": 0.0, "end": 60.0, "bpm": 124.0, "first_downbeat": 0.4}],
        "events": [{"t": 10.0, "kind": "drop"}],
    }


def test_a_map_is_valid_only_with_the_current_version_and_the_fields_the_design_reads() -> None:
    assert valid_map(good_map(), music.MAP_VERSION)
    assert not valid_map(good_map(), music.MAP_VERSION + 1)
    for broken in (
        {**good_map(), "songs": []},
        {**good_map(), "songs": [{"start": 0.0}]},
        {
            **good_map(),
            "songs": [{"start": 0.0, "end": 1.0, "bpm": float("nan"), "first_downbeat": 0}],
        },
        {**good_map(), "events": [{"kind": "drop"}]},
        {**good_map(), "events": "x"},
        {**good_map(), "duration": 0},
        {**good_map(), "duration": True},
        {**good_map(), "events": [{"t": 1.0}] * (mixanalysis.MAX_EVENTS + 1)},
        {**good_map(), "pad": "x" * (mixanalysis.MAX_MAP_BYTES + 1)},
        [],
        None,
    ):
        assert not valid_map(broken, music.MAP_VERSION)
