"""The ADR 0004 guards (``crypto_scan``) over the whole source tree, and proof they can fail."""

from pathlib import Path

import pytest

from crypto_scan import scan_source

SRC = Path(__file__).resolve().parent.parent / "src"


def _module(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def test_only_the_allowed_modules_use_the_envelope_and_the_key_tables() -> None:
    violations = []
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        module = _module(path)
        violations += [(module, line, why) for line, why in scan_source(path.read_text(), module)]
    assert violations == []


def test_the_envelope_is_imported_by_exactly_the_three_users() -> None:
    importers = set()
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        text = path.read_text()
        if "classbeat.crypto.envelope import" in text:
            importers.add(_module(path).split(".")[1])
    assert importers == {"auth", "connections", "crypto"}


ALLOWED = [
    ("from classbeat.crypto.envelope import seal", "classbeat.connections.repository"),
    ("from classbeat.crypto.envelope import seal", "classbeat.auth.totp_keys"),
    ("from classbeat.crypto.envelope import check_value", "classbeat.crypto.keks"),
    ("from .envelope import seal", "classbeat.crypto.keks"),
    ("from classbeat.crypto.keks import KeyRing, load_keyring", "classbeat.web"),
    ("from classbeat.crypto import keks\nkeks.load_keyring", "classbeat.keys_cli"),
    ("x = 'the connections of a user'", "classbeat.web"),
    ("x = 'connection_keks'", "classbeat.db.tables"),
    ("from classbeat import connections\nconnections.save", "classbeat.auth.deps"),
    ("x = 'SELECT 1 FROM connection_keks'", "classbeat.crypto.keks"),
    ("x = 'DELETE FROM connections'", "classbeat.connections.repository"),
    ("x = 'VACUUM (FULL) public.connections'", "classbeat.connections.repository"),
    ("x = 'select the right connections from the list'", "classbeat.web"),
]
REFUSED = [
    ("from classbeat.crypto.envelope import seal", "classbeat.web"),
    ("import classbeat.crypto.envelope", "classbeat.jobs.run"),
    ("import classbeat.crypto.envelope as e", "classbeat.keys_cli"),
    ("from classbeat.crypto import envelope", "classbeat.worker.loop"),
    ("from . import envelope", "classbeat.crypto.other"),
    ("from .crypto.envelope import open_sealed", "classbeat.cli"),
    ("import importlib\nimportlib.import_module('classbeat.crypto.envelope')", "classbeat.cli"),
    ("from classbeat.crypto.keks import connection_keks", "classbeat.keys_cli"),
    ("from classbeat.crypto.keks import metadata", "classbeat.connections.repository"),
    ("from classbeat.crypto import keks\nkeks.connection_keks", "classbeat.keys_cli"),
    ("import classbeat.crypto.keks as k\nk.connection_keks", "classbeat.keys_cli"),
    ("x = 'DELETE FROM connection_keks'", "classbeat.keys_cli"),
    ("x = 'VACUUM (FULL) connection_keks'", "classbeat.keys_cli"),
    ('x = f"SELECT * FROM public.connection_keks WHERE key_id = {k}"', "classbeat.web"),
    ("from classbeat.connections.tables import connections", "classbeat.auth.deps"),
    ("from classbeat.connections import tables", "classbeat.keys_cli"),
    ("import classbeat.connections.tables", "classbeat.web"),
    ("x = 'SELECT secret FROM connections WHERE id = :id'", "classbeat.keys_cli"),
    ("x = 'UPDATE public.connections SET status = 1'", "classbeat.auth.mfa"),
    ("x = 'VACUUM (FULL) public.connections'", "classbeat.keys_cli"),
]


@pytest.mark.parametrize(("source", "module"), ALLOWED)
def test_allowed(source: str, module: str) -> None:
    assert scan_source(source, module) == []


@pytest.mark.parametrize(("source", "module"), REFUSED)
def test_refused(source: str, module: str) -> None:
    assert scan_source(source, module) != []
