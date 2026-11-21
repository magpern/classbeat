"""Migration 0070 (ADR 0042 section 7.1, #1393): ``connections.granted_scopes``. A Spotify row from
before it reads ``NULL``, which ``preview_granted`` reports as false; the CHECK allows the column on
``spotify`` rows only and in ``stored_scopes``' shape; the downgrade drops it. Never the network."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from classbeat.spotify.flow import preview_granted

BACKEND_DIR = Path(__file__).resolve().parent.parent
PREVIEW = (
    "playlist-modify-private playlist-modify-public playlist-read-private "
    "user-modify-playback-state user-read-playback-state"
)
INSERT = text(
    "INSERT INTO connections (owner_id, provider, status, secret, secret_nonce, wrapped_dek, "
    "dek_nonce, key_id, public) VALUES (:o, :p, 'connected', '\\x01', :n, :w, :n, 'kek-1', "
    "CAST(:public AS jsonb))"
)


def _config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


def _has_column(url: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return (
                connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                        "AND table_name = 'connections' AND column_name = 'granted_scopes'"
                    )
                ).first()
                is not None
            )
    finally:
        engine.dispose()


def test_0070_adds_granted_scopes_null_for_old_rows_and_the_downgrade_drops_it(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = _config()
    admin = create_engine(database_url)
    owner = uuid.uuid4()
    zeros = {"n": bytes(12), "w": bytes(48)}
    try:
        command.upgrade(config, "0069_mix_too_long")
        assert not _has_column(database_url)
        with admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, email_normalized, email_verified_at) "
                    "VALUES (:o, :e, :e, now())"
                ),
                {"o": owner, "e": f"m70-{owner.hex[:8]}@example.com"},
            )
            for provider in ("spotify", "mowl"):
                connection.execute(
                    INSERT,
                    {"o": owner, "p": provider, "public": '{"client_id": "a"}', **zeros},
                )
        command.upgrade(config, "0070_spotify_granted_scopes")
        assert _has_column(database_url)
        read = text("SELECT granted_scopes FROM connections WHERE owner_id = :o AND provider = :p")
        with admin.begin() as connection:
            before = connection.execute(read, {"o": owner, "p": "spotify"}).scalar_one()
        assert before is None
        assert preview_granted(before) is False

        put = text(
            "UPDATE connections SET granted_scopes = :s WHERE owner_id = :o AND provider = :p"
        )
        with admin.begin() as connection:
            connection.execute(put, {"s": PREVIEW, "o": owner, "p": "spotify"})
            assert preview_granted(
                connection.execute(read, {"o": owner, "p": "spotify"}).scalar_one()
            )
        for provider, bad in (
            ("mowl", PREVIEW),  # only a Spotify row holds one
            ("spotify", ""),
            ("spotify", " playlist-read-private"),
            ("spotify", "playlist-read-private  user-read-playback-state"),
            ("spotify", "Playlist-Read-Private"),
            ("spotify", "playlist-read-private;drop"),
        ):
            with (
                pytest.raises(IntegrityError, match="connections_granted_scopes"),
                admin.begin() as connection,
            ):
                connection.execute(put, {"s": bad, "o": owner, "p": provider})

        command.downgrade(config, "0069_mix_too_long")
        assert not _has_column(database_url)
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM connections WHERE owner_id = :o"), {"o": owner})
            connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
        admin.dispose()
        command.downgrade(config, "base")
