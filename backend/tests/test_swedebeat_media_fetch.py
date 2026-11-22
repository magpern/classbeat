"""A3 of ADR 0016 without a database: the media fetch (``classbeat.design.mixfetch``) against the
fake media endpoint with an injected list, and the private directory and work root. In-process
fakes and loopback only. (The limited analysis child and the decode cap went with the local
analysis, #1357; the render child's limits are in ``test_studio_render*.py``.)
"""

import io
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from classbeat.design import mixfetch
from classbeat.egress import Allowlist, mowl_media_test_client
from classbeat.jobs import JobFailure, JobRun, RunStopped
from classbeat.log import QUIET_LOGGERS, configure_logging
from mediafake import FakeMedia

# ruff: noqa: ASYNC240, PT012, PT018, SIM117

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
    # none of our headers; the one coding we can read, said outright (RFC 9110 12.5.3)
    assert set(sent) == {"host", "accept-encoding"}
    assert sent["accept-encoding"] == "identity"


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
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    for name in ("mix-aaaa", "mix-bbbb"):
        (root / name).mkdir()
        (root / name / "mix.audio").write_bytes(b"x")
    (root / "other").mkdir()  # not ours to judge: kept
    assert mixfetch.sweep_work_root(root) == 2
    assert sorted(p.name for p in root.iterdir()) == ["other"]
    assert mixfetch.sweep_work_root(tmp_path / "missing") == 0


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
