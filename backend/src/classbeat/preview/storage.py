"""The private preview volume (ADR 0042 section 6.5): where the source-track cache and the clips
live, per owner. Filesystem only, no database.

``CLASSBEAT_PREVIEW_DIR`` names the root; unset, the audition is off (web and worker
``configure`` it at start). The root and every directory under it are real directories (no
symlink), mode 0700 and owned by this process's user (``mixfetch.check_private``), so web and
worker must run as the same user over the same volume.

Layout, every name derived from ids, never from input:

    <root>/<owner_id>/sources/<preview_sources.id>.mp3
    <root>/<owner_id>/clips/<preview_clips.id>.mp3

A file is written under a temporary name in its own directory and renamed into place
(``place``), so a reader sees a whole file or none. A file is placed **before** its row is
inserted; a crash between the two leaves a file with no row, which the orphan sweep removes
(``purge``). ``remove`` and ``remove_owner`` never raise.
"""

import contextlib
import hashlib
import logging
import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Final, Literal
from uuid import UUID

from classbeat.design.mixfetch import check_private

logger = logging.getLogger(__name__)

Kind = Literal["sources", "clips"]
KINDS: Final[tuple[Kind, ...]] = ("sources", "clips")
SUFFIX: Final = ".mp3"
TEMP_PREFIX: Final = ".tmp-"
CHUNK: Final = 1 << 20

DEFAULT_OWNER_MAX_BYTES: Final = 512 * 1024 * 1024

_root: Path | None = None
_owner_max_bytes: int = DEFAULT_OWNER_MAX_BYTES


def configure(root: Path | None, owner_max_bytes: int = DEFAULT_OWNER_MAX_BYTES) -> None:
    """Set the root (``None``: the audition is off) and the per-owner size cap."""
    global _root, _owner_max_bytes
    _root, _owner_max_bytes = root, owner_max_bytes


def current() -> Path | None:
    return _root


def owner_max_bytes() -> int:
    return _owner_max_bytes


def _private_dir(path: Path) -> Path:
    with contextlib.suppress(FileExistsError):
        path.mkdir(mode=0o700)
    check_private(path)
    return path


def owner_dir(root: Path, owner_id: UUID) -> Path:
    return root / str(UUID(str(owner_id)))


def path_of(root: Path, owner_id: UUID, kind: Kind, file_id: UUID) -> Path:
    """Where a row's file is (nothing is created)."""
    if kind not in KINDS:
        raise ValueError("kind")
    return owner_dir(root, owner_id) / kind / f"{UUID(str(file_id))}{SUFFIX}"


def kind_dir(root: Path, owner_id: UUID, kind: Kind) -> Path:
    """The owner's directory of ``kind``, created private when missing."""
    check_private(root)
    return _private_dir(_private_dir(owner_dir(root, owner_id)) / kind)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def copy_checked(source: Path, dest: Path, sha256: str | None = None) -> str | None:
    """Copy ``source`` to the new file ``dest`` (mode 0600), hashing on the way; the copy's
    SHA-256, or ``None`` (and no ``dest``) when it is not ``sha256`` or the copy failed."""
    digest = hashlib.sha256()
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            while chunk := reader.read(CHUNK):
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except OSError:
        remove(dest)
        return None
    found = digest.hexdigest()
    if sha256 is not None and found != sha256:
        remove(dest)
        return None
    return found


def place(source: Path, root: Path, owner_id: UUID, kind: Kind, file_id: UUID) -> Path | None:
    """Copy ``source`` into the owner's ``kind`` directory as ``file_id``'s file, atomically; the
    file's path, or ``None`` when it could not be written (nothing is left behind)."""
    try:
        directory = kind_dir(root, owner_id, kind)
    except OSError:
        logger.warning("preview volume refused")
        return None
    final = path_of(root, owner_id, kind, file_id)
    temp = directory / f"{TEMP_PREFIX}{secrets.token_hex(8)}"
    if copy_checked(source, temp) is None:
        return None
    try:
        os.replace(temp, final)
    except OSError:
        remove(temp)
        return None
    return final


def remove(path: Path) -> bool:
    """Delete one file; whether it was there. Never raises."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("preview file not removed")
        return False
    return True


def remove_owner(root: Path | None, owner_id: UUID) -> None:
    """Delete everything of one owner (their account is gone). Never raises."""
    if root is None:
        return
    target = owner_dir(root, owner_id)
    if target.is_symlink() or (sys.platform == "win32" and target.is_junction()):
        return  # never followed; the sweep logs it
    shutil.rmtree(target, ignore_errors=True)
