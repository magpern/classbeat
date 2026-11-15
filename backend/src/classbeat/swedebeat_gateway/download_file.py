"""The track download's side of the disk (ADR 0036 sections 3.4 and 8, SR C2): the new file and the
capped, raw read of the answer into it. ``client.download_track`` is its only caller.

- **The file** (``create``) is new (``O_EXCL``, never a link: ``O_NOFOLLOW`` where the system has
  it), mode 0600, in a directory that is a real directory (not a link or junction) and, on POSIX,
  this user's with mode 0700 (``design.mixfetch.private_directory`` makes such directories). Any
  other destination is a ``ValueError`` before a request is sent.
- **The answer** (``receive``): a status other than ``200`` is a ``Refused`` with that status (a
  ``401``'s body, capped, for its ``detail``; a ``429``'s ``Retry-After``). A ``200`` must be
  ``audio/mpeg`` with identity encoding only and a well-formed ``Content-Length`` within the cap,
  else ``invalid_response`` (``track_too_large`` over the cap) before a byte is read. The body is
  read raw (``aiter_raw``: httpx would decode gzip on read, and a small answer can decode to
  gigabytes) and counted: past the cap the read stops at once (``track_too_large``), whatever
  ``Content-Length`` said. An empty body or one shorter or longer than declared is
  ``invalid_response``. A write, a flush or the close that fails is ``internal``, and the file
  is removed all the same. The sha256 is taken as it is written.

Nothing here logs, and no message carries the reference, the bearer or the key.
"""

import asyncio
import contextlib
import hashlib
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import httpx

from classbeat.connections.errors import retry_after_of
from classbeat.swedebeat_gateway.answer import read_capped
from classbeat.swedebeat_gateway.track_policy import declared_length, is_audio_mpeg, is_identity

_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
_FLAGS |= getattr(os, "O_BINARY", 0)


@dataclass(frozen=True, slots=True)
class Download:
    """A downloaded track: the hex sha256 of its bytes and their count."""

    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class Refused:
    """No usable track: the gateway's status (``None`` when the answer itself was refused, with
    ``category``), a ``401``'s body and a ``429``'s ``Retry-After``."""

    status: int | None
    category: str = "invalid_response"
    body: bytes | None = field(default=None, repr=False)
    retry_after: int | None = None


def _private(directory: Path) -> bool:
    try:
        info = os.lstat(directory)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or directory.is_junction():
        return False
    if sys.platform == "win32":
        return True
    return info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700


def create(dest_path: Path) -> BinaryIO:
    """``dest_path`` opened as a new file for writing; ``ValueError`` for any other destination."""
    if not _private(dest_path.parent):
        raise ValueError("a track is downloaded into a private directory")
    try:
        fd = os.open(dest_path, _FLAGS, 0o600)
    except OSError:
        raise ValueError("a track is downloaded into a new file") from None
    return os.fdopen(fd, "wb")


def close(out: BinaryIO) -> bool:
    """Close the file; ``False`` when that fails (its buffered bytes may be lost): the caller
    then counts the download as ``internal`` and discards the file. Never raises."""
    try:
        out.close()
    except OSError:
        return False
    return True


def discard(dest_path: Path) -> None:
    """Remove the file ``create`` made (a refused or cut download leaves nothing behind)."""
    with contextlib.suppress(OSError):  # gone already, or nothing more can be done
        os.unlink(dest_path)


async def _refused(response: httpx.Response, error_cap: int) -> Refused:
    status = response.status_code
    if status == 401:
        return Refused(status, body=await read_capped(response, error_cap))
    if status == 429:
        return Refused(status, retry_after=retry_after_of(response))
    return Refused(status)


async def receive(
    response: httpx.Response, out: BinaryIO, max_bytes: int, error_cap: int
) -> Download | Refused:
    """The answer's body into ``out`` (module docstring), or why not."""
    if response.status_code != 200:
        return await _refused(response, error_cap)
    headers = response.headers
    if not is_audio_mpeg(headers.get("content-type")):
        return Refused(None)
    if not is_identity(headers.get_list("content-encoding")):
        return Refused(None)
    try:
        declared = declared_length(headers.get("content-length"))
    except ValueError:
        return Refused(None)
    if declared is not None and declared > max_bytes:
        return Refused(None, "track_too_large")
    digest = hashlib.sha256()
    total = 0
    async for chunk in response.aiter_raw():
        total += len(chunk)
        if total > max_bytes:
            return Refused(None, "track_too_large")
        digest.update(chunk)
        try:
            await asyncio.to_thread(out.write, chunk)
        except OSError:
            return Refused(None, "internal")
    if total == 0 or (declared is not None and total != declared):
        return Refused(None)
    try:
        await asyncio.to_thread(out.flush)
    except OSError:
        return Refused(None, "internal")
    return Download(digest.hexdigest(), total)
