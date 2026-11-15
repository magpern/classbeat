"""The database URL rules of ``classbeat.config`` (moved out of it, #1077). Pure: no I/O.

``classbeat.config.Settings`` validates its three database URLs with ``check_database_url`` and
puts a password file's value into a URL with ``with_password``. Re-exported from
``classbeat.config``, so callers keep importing ``DATABASE_DRIVER`` from there.
"""

from typing import Final

from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

DATABASE_DRIVER: Final = "postgresql+psycopg"


def check_database_url(value: SecretStr | None, field_name: str) -> SecretStr | None:
    """``value`` if it is a ``postgresql+psycopg://`` URL; the message names the field only."""
    if value is None:
        return None
    try:
        url = make_url(value.get_secret_value())
    except ArgumentError:
        raise ValueError(f"{field_name} is not a valid database URL") from None
    if url.drivername != DATABASE_DRIVER:
        raise ValueError(f"{field_name} must use the {DATABASE_DRIVER}:// scheme")
    return value


def with_password(url: SecretStr, password: SecretStr | None) -> URL:
    """``url`` with ``password`` substituted when it is set."""
    result = make_url(url.get_secret_value())
    if password is not None:
        result = result.set(password=password.get_secret_value())
    return result
