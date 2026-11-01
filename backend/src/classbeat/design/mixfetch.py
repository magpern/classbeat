"""Fetch a Swedebeat mix's audio from the MOWL temporary link (ADR 0016 section 4, T109).

The link is a **bearer capability**: whoever holds the URL can read the file. So it is used once,
in the worker, with none of our headers, and it exists only in the variables of this module's
functions: it is never logged (``query string and signature included``: only the host is, by
``classbeat.egress`` and below), stored (job input, checkpoint, result, event, error), returned or
put in an exception message (every failure here is a ``JobFailure`` carrying a category and nothing
chained to it).

- **One client per fetch** (``client_factory()``, ``egress.mowl_media_async_client`` unless a test
  passes its own): it keeps a cookie jar and a pool, which must not outlive the link. The client
  follows no redirect; it reaches only the media list (today empty: ``media_host_not_allowed`` and
  not a single request); it sends none of our headers.
- **Limits.** The answer must be ``200``; ``Content-Length`` and the streamed count are both held
  to ``MAX_BYTES`` (250 MB); the whole fetch to the client's 300 s total deadline. The **raw bytes**
  are read (``aiter_raw``) and any ``Content-Encoding`` but identity is refused: httpx would decode
  gzip, deflate, br or zstd on read, and a small answer can decode to gigabytes (a compression
  bomb). The content type is not trusted either way; the decoder decides what is audio, later.
- **A private directory** (mode 0700 under ``work_root``) holds the file; ``private_directory``
  removes it in a ``finally`` (success, error, cancel), and ``sweep_work_root`` empties the root at
  worker start (a crash leaves a directory behind; this removes it).
- **Categories**: ``media_host_not_allowed`` (the allowlist refused the link or a hop of it),
  ``media_refused`` (a status other than the retried ones, a redirect, a compressed, empty or too
  large answer, a link that is not a URL), ``upstream_unavailable`` (a 429 or 5xx, a timeout, a cut
  connection: retried), ``internal`` (our own disk).
"""

import asyncio
import contextlib
import logging
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Final

import httpx

from classbeat.egress import OutboundBlocked, RequestDeadline, mowl_media_async_client
from classbeat.jobs import JobFailure, JobRun, RunStopped

logger = logging.getLogger(__name__)

MAX_BYTES: Final = 250 * 1024 * 1024
MAX_LINK_CHARACTERS: Final = 4096
CHECKPOINT_SECONDS: Final = 5.0
ROOT_NAME: Final = "classbeat-mix-audio"
PREFIX: Final = "mix-"
_HOST_SHAPE = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")


def work_root() -> Path:
    """The parent of every private directory: ``<temp>/classbeat-mix-audio`` (mode 0700)."""
    root = Path(tempfile.gettempdir()) / ROOT_NAME
    root.mkdir(mode=0o700, exist_ok=True)
    return root


@contextlib.contextmanager
def private_directory(root: Path | None = None) -> Iterator[Path]:
    """A new directory (0700) for one fetch, removed with everything in it on the way out."""
    path = Path(tempfile.mkdtemp(prefix=PREFIX, dir=root or work_root()))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def sweep_work_root(root: Path | None = None) -> int:
    """At worker start: remove every directory a crashed run left behind; the number removed."""
    base = root or Path(tempfile.gettempdir()) / ROOT_NAME
    removed = 0
    if base.is_dir():
        for entry in base.iterdir():
            if entry.name.startswith(PREFIX):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
    return removed


def link_host(link: str) -> str:
    """The host of ``link`` for the operator log: lower case DNS characters only (never the path,
    query, signature or userinfo), or ``invalid``."""
    try:
        host = httpx.URL(link).host.lower()
    except (httpx.InvalidURL, ValueError):
        return "invalid"
    return host if _HOST_SHAPE.fullmatch(host) else "invalid"


def _identity(headers: httpx.Headers) -> bool:
    """Whether the answer declares no content coding (or only ``identity``)."""
    for value in headers.get_list("content-encoding"):
        for token in value.split(","):
            if token.strip().lower() not in ("", "identity"):
                return False
    return True


def _size(headers: httpx.Headers) -> int | None:
    value = headers.get("content-length")
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or len(value) > 15:
        raise JobFailure("media_refused")
    return int(value)


async def _fetch(
    run: JobRun,
    client: httpx.AsyncClient,
    link: str,
    target: Path,
    max_bytes: int,
    clock: Callable[[], float],
) -> int:
    async with client.stream("GET", link) as response:
        status = response.status_code
        if status != 200:
            if status == 429 or status >= 500:
                raise JobFailure("upstream_unavailable")
            raise JobFailure("media_refused")  # a redirect included: none is followed
        if not _identity(response.headers):
            raise JobFailure("media_refused")
        declared = _size(response.headers)
        if declared is not None and declared > max_bytes:
            raise JobFailure("media_refused")
        total = 0
        next_checkpoint = clock() + CHECKPOINT_SECONDS
        with target.open("wb") as out:
            async for chunk in response.aiter_raw():
                total += len(chunk)
                if total > max_bytes:
                    raise JobFailure("media_refused")
                await asyncio.to_thread(out.write, chunk)
                if clock() >= next_checkpoint:
                    next_checkpoint = clock() + CHECKPOINT_SECONDS
                    await run.checkpoint()
        if total == 0 or (declared is not None and total != declared):
            raise JobFailure("media_refused")
        return total


async def fetch_mix_audio(
    run: JobRun,
    link: str,
    directory: Path,
    *,
    client_factory: Callable[[], httpx.AsyncClient] = mowl_media_async_client,
    max_bytes: int = MAX_BYTES,
    clock: Callable[[], float] = time.monotonic,
) -> Path:
    """Fetch ``link`` into ``directory`` (``mix.audio``) and return the file's path.

    Raises ``JobFailure`` with a category from the module docstring, and ``RunStopped`` from a
    checkpoint (a cancel, a lost lease, a shutdown) after closing the connection. The link, a
    signature or a response's text is in no message and no log."""
    if not isinstance(link, str) or not 0 < len(link) <= MAX_LINK_CHARACTERS:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise JobFailure("media_refused")
    target = directory / "mix.audio"
    started = clock()
    host = link_host(link)
    try:
        async with client_factory() as client:  # one client per fetch
            size = await _fetch(run, client, link, target, max_bytes, clock)
    except (JobFailure, RunStopped):
        raise
    except OutboundBlocked:
        # ``egress.blocked`` logged the refused host; nothing else of the link is named.
        raise JobFailure("media_host_not_allowed") from None
    except (RequestDeadline, TimeoutError, httpx.TransportError):
        raise JobFailure("upstream_unavailable") from None
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        raise JobFailure("media_refused") from None  # a link that is no URL and the like
    except Exception:
        raise JobFailure("internal") from None
    logger.info(
        "swedebeat media fetched",
        extra={"host": host, "bytes": size, "seconds": round(clock() - started, 1)},
    )
    return target
