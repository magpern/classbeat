"""Reading secret files: the secrets directory, the file reader and the two settings sources.

The mechanism only. Which fields are secrets, which of them are optional and which are read
byte for byte stay in ``classbeat.config`` next to ``Settings`` (the admin role's password is named
in no other module, ``tests/test_maint_privileges.py``), and are passed in. Re-exported from
``classbeat.config``, so callers keep importing them from there.
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# Pinned to the name this logger had in ``classbeat.config``: operators' log filters and the
# warning test (tests/test_secret_files.py) look for it there.
logger = logging.getLogger("classbeat.config")

DEFAULT_SECRETS_DIR = "/run/secrets"


def secrets_dir() -> Path:
    """Where secret files live: ``CLASSBEAT_SECRETS_DIR``, default ``/run/secrets``."""
    return Path(os.environ.get("CLASSBEAT_SECRETS_DIR") or DEFAULT_SECRETS_DIR)


def read_files(
    directory: Path,
    names: tuple[str, ...],
    *,
    optional: frozenset[str],
    byte_exact: frozenset[str],
) -> dict[str, str]:
    """Read ``<directory>/<name>`` for each name; missing files and directories are skipped.

    A file of a name in ``optional`` that cannot be read (its mode or group, #511) counts as not
    set, and one warning names the field, never the path's content; any other unreadable file
    raises. A file holding only whitespace counts as empty, except the names in ``byte_exact``
    (``classbeat.config.read_secret_files`` passes the three database passwords, which
    ``10-roles.sql`` and ``31-maint-password.psql`` read byte for byte the same way).
    """
    if not directory.is_dir():
        return {}
    values: dict[str, str] = {}
    for name in names:
        path = directory / name
        if path.is_file():
            try:
                raw = path.read_bytes()
            except OSError:
                if name not in optional:
                    raise
                logger.warning("secret file %s is not readable; treated as not set", name)
                continue
            text = raw.decode("utf-8")
            # Only the trailing newline an editor or `echo` adds; spaces may be part of a secret.
            text = text.removesuffix("\n").removesuffix("\r")
            if name not in byte_exact and not text.strip():
                text = ""  # a blank file is the empty placeholder: that secret is off
            values[name] = text
    return values


class SecretFilesSettingsSource(PydanticBaseSettingsSource):
    """Secrets come only from files, named after the field without the env prefix.

    ``/run/secrets/database_password`` sets ``database_password``. pydantic-settings' own
    secrets source would look for ``classbeat_database_password`` because of ``env_prefix``.
    ``read`` reads the files on each call (so the directory is looked up when settings load).
    """

    def __init__(
        self, settings_cls: type[BaseSettings], read: Callable[[], dict[str, str]]
    ) -> None:
        super().__init__(settings_cls)
        self._read = read

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # unused: __call__ reads the files directly

    def __call__(self) -> dict[str, Any]:
        return dict(self._read())


class WithoutSecretsSource(PydanticBaseSettingsSource):
    """Wraps the environment source and drops the secret fields: env vars never set secrets."""

    def __init__(self, inner: PydanticBaseSettingsSource, secret_fields: tuple[str, ...]) -> None:
        super().__init__(inner.settings_cls)
        self._inner = inner
        self._secret_fields = secret_fields

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._inner.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._inner().items() if k not in self._secret_fields}
