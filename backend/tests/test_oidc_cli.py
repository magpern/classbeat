"""``python -m classbeat oidc keys`` and ``admin player-subject`` (ADR 0034 section 1)."""

import argparse
import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from joserfc.jwk import ECKey
from sqlalchemy import create_engine, text

from authtools import Env, make_settings
from classbeat.cli import add_subcommands, run_command
from oidctools import OIDC_SETTINGS, expected_subject, keyset_json, new_pem, write_provider_files


class Pipe(io.StringIO):
    def __init__(self, tty: bool = False) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_subcommands(parser)
    return parser.parse_args(argv)


def keys(step: str, keyset: str, *extra: str, tty: bool = False) -> tuple[int, str, str]:
    from classbeat.oidc_cli import run_keys

    out, err = Pipe(tty), io.StringIO()
    code = run_keys(parse("oidc", "keys", step, *extra), out, err, io.StringIO(keyset))
    return code, out.getvalue(), err.getvalue()


def test_add_next_on_an_empty_set_makes_a_current_key() -> None:
    code, out, err = keys("add-next", "", "--kid", "k1")
    assert (code, err) == (0, "")
    [entry] = json.loads(out)["keys"]
    assert (entry["kid"], entry["state"]) == ("k1", "current")
    assert ECKey.import_key(entry["pem"]).is_private  # a P-256 private key in PEM


def test_a_rotation_publishes_then_promotes_then_retires() -> None:
    old = datetime.now(UTC) - timedelta(days=3)
    current = keyset_json(("k1", "current", new_pem(), old))
    code, added, _ = keys("add-next", current, "--kid", "k2")
    assert code == 0
    code, _, err = keys("promote", added)
    assert code == 1
    assert "24 hours and 5 minutes" in err
    aged = json.loads(added)
    aged["keys"][1]["published_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    code, promoted, _ = keys("promote", json.dumps(aged))
    assert code == 0
    assert [(k["kid"], k["state"]) for k in json.loads(promoted)["keys"]] == [
        ("k1", "retired"), ("k2", "current")
    ]  # fmt: skip
    code, kept, _ = keys("retire", promoted)
    assert code == 0
    assert len(json.loads(kept)["keys"]) == 2
    code, listing, _ = keys("list", promoted, tty=True)
    assert code == 0
    assert "PRIVATE" not in listing
    assert "BEGIN" not in listing
    assert listing.splitlines()[0].startswith("k1 retired ")
    assert "published" in listing


def test_a_step_that_writes_keys_refuses_a_terminal() -> None:
    for step in ("add-next", "promote", "retire"):
        code, out, err = keys(step, "", tty=True)
        assert code == 1
        assert out == ""
        assert "terminal" in err


def test_a_bad_key_set_is_refused_without_showing_it() -> None:
    code, out, err = keys("list", '{"keys": [{"kid": "k", "pem": "SECRETPEM"}]}')
    assert code == 1
    assert out == ""
    assert "SECRETPEM" not in err
    bad = keyset_json(("k1", "current", "-----BEGIN PRIVATE KEY-----\nSECRET\n", datetime.now(UTC)))
    code, out, err = keys("retire", bad)
    assert code == 1
    assert out == ""
    assert "SECRET\n" not in err


@pytest.fixture
def subject_cli(env: Env, app_database_url: str, owner_database_url: str, kek_dir: Path) -> Any:
    write_provider_files(kek_dir)
    settings = make_settings(
        app_database_url, migrations_database_url=owner_database_url, **OIDC_SETTINGS
    )

    def run(*argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        return run_command(parse(*argv), settings, out, err), out.getvalue(), err.getvalue()

    return run


def audit_rows(database_url: str) -> list[Any]:
    """``admin_audit`` as the superuser: the app role has no privilege on it."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        "SELECT action, target_user_id, target_kind, target_id, outcome, params, "
                        "actor_email FROM admin_audit"
                    )
                ).all()
            )
    finally:
        engine.dispose()


async def test_player_subject_prints_the_subject_and_audits(
    env: Env, subject_cli: Any, database_url: str
) -> None:
    user_id = await env.user("rider@example.com")
    code, out, err = await asyncio.to_thread(
        subject_cli, "admin", "player-subject", "Rider@Example.com"
    )
    assert (code, err) == (0, "")
    assert out.strip() == expected_subject(user_id)
    [row] = audit_rows(database_url)
    assert (row.action, row.target_user_id, row.target_kind, row.outcome) == (
        "player.subject", user_id, "user", "ok"
    )  # fmt: skip
    assert (row.target_id, row.params, row.actor_email) == (str(user_id), {}, "cli")


async def test_player_subject_refuses_an_unknown_user(
    env: Env, subject_cli: Any, database_url: str
) -> None:
    code, out, err = await asyncio.to_thread(subject_cli, "admin", "player-subject", "no@x.se")
    assert (code, out) == (1, "")
    assert "no such user" in err
    assert audit_rows(database_url) == []


async def test_player_subject_refuses_when_the_provider_is_off(
    env: Env, app_database_url: str, owner_database_url: str
) -> None:
    await env.user("rider@example.com")
    settings = make_settings(app_database_url, migrations_database_url=owner_database_url)
    out, err = io.StringIO(), io.StringIO()
    args = parse("admin", "player-subject", "rider@example.com")
    code = await asyncio.to_thread(run_command, args, settings, out, err)
    assert code == 1
    assert "not configured" in err.getvalue()
