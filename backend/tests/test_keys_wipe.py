"""``keys wipe`` after a suspected KEK compromise (ADR 0004 section 2 and the Architect's
decisions on #65): refusals, per-user wipe and mail, ``--resend``, re-runs."""

import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from authtools import TEST_KEK, Env
from classbeat.classes import service as classes_api
from classbeat.db import (
    create_migrations_engine,
    create_session_factory,
    system_session,
    user_session,
)
from ctx import make_context
from keystools import (
    KEY_2,
    KEY_3,
    TOKENS,
    add_totp,
    cli,
    connect,
    owner_count,
    ring_for,
    rotate,
    settings_for,
    token_of,
)
from smtpfake import SmtpFake


@pytest.fixture
def wipe_settings(app_database_url: str, owner_database_url: str, smtp: SmtpFake) -> Any:
    def make(kek_id: str = "kek-3", **overrides: Any):
        values: dict[str, Any] = {
            "migrations_database_url": owner_database_url,
            "smtp_host": "127.0.0.1",
            "smtp_port": smtp.port,
            "smtp_tls": "none",
            **overrides,
        }
        return settings_for(app_database_url, kek_id, **values)

    return make


async def stop_app(env: Env) -> None:
    """Web and worker are stopped before a wipe: close the app role's pooled connections."""
    await env.app.state.event_hub.stop()  # web's LISTEN connection goes with it
    await env.app.state.engine.dispose()


async def events(env: Env) -> list[dict[str, Any]]:
    async with system_session(env.factory) as db:
        rows = await db.execute(
            text(
                "SELECT user_id, detail FROM auth_events WHERE kind = 'connections_wiped' "
                "ORDER BY at, id"
            )
        )
        return [{"user_id": r.user_id, **r.detail} for r in rows]


async def test_wipe_refuses_while_the_app_role_is_connected(env: Env, wipe_settings: Any) -> None:
    await env.scalar("SELECT 1")  # the app's pool holds a connection
    code, _, err = await cli(wipe_settings(), "keys", "wipe", "--confirm")
    assert code == 1
    assert "the app role is still connected" in err


async def test_wipe_refuses_while_the_configured_key_id_has_a_check_row(
    env: Env, wipe_settings: Any
) -> None:
    await stop_app(env)
    code, _, err = await cli(wipe_settings("kek-1"), "keys", "wipe", "--confirm")
    assert code == 1
    assert "already has a check row" in err


def test_wipe_needs_a_mode() -> None:
    from keystools import parse

    with pytest.raises(SystemExit):
        parse("keys", "wipe")
    with pytest.raises(SystemExit):
        parse("keys", "wipe", "--confirm", "--resend")


async def test_rotate_rewrap_wipe_leaves_nothing_and_a_fresh_connect_works(
    env: Env,
    app_database_url: str,
    owner_database_url: str,
    kek_dir: Path,
    wipe_settings: Any,
    smtp: SmtpFake,
) -> None:
    alice = await env.user("a@example.com")  # a connection, no TOTP
    bob = await env.user("b@example.com", role="admin")  # a connection and TOTP
    carol = await env.user("c@example.com")  # neither
    dave = await env.user("d@example.com")  # TOTP only
    for email, user_id in (("a@example.com", alice), ("b@example.com", bob)):
        await connect(env.factory, env.app.state.keyring, user_id, TOKENS[email])
    await add_totp(env, env.app.state.keyring, bob)
    await add_totp(env, env.app.state.keyring, dave, v1=True)
    await env.login(bob)
    # Rotate to kek-2 and rewrap; then kek-2 is suspected leaked: kek-3 replaces it.
    rotate(kek_dir, "kek-2", KEY_2, old_id="kek-1")
    code, _, _ = await cli(settings_for(app_database_url, "kek-2"), "keys", "rewrap")
    assert code == 0
    # The runbook: a new KEK under a new id, the old files removed.
    (kek_dir / "connection_kek_kek-1").unlink()
    (kek_dir / "connection_kek").write_bytes(KEY_3.hex().encode())
    await stop_app(env)

    code, out, err = await cli(wipe_settings("kek-3"), "keys", "wipe", "--confirm")
    assert (code, err) == (0, ""), err
    assert "wiped: 3" in out
    assert "nothing to wipe: 1" in out
    assert "connections deleted: 2" in out
    assert "TOTP reset: 2" in out
    assert "mails sent: 3" in out
    assert "KEK check rows deleted: 2" in out
    assert "CHECKPOINT" in out  # done, or the exact step to run as postgres
    for secret in ("@example.com", *TOKENS.values(), TEST_KEK.hex(), KEY_2.hex(), str(alice)):
        assert secret not in out

    admin = create_migrations_engine(wipe_settings())
    try:
        async with system_session(create_session_factory(admin)) as db:
            assert (await db.execute(text("SELECT count(*) FROM connection_keks"))).scalar() == 0
            assert (await db.execute(text("SELECT count(*) FROM mfa_totp"))).scalar() == 0
            assert (await db.execute(text("SELECT count(*) FROM sessions"))).scalar() == 0
        for user_id in (alice, bob, carol, dave):
            assert await owner_count(create_session_factory(admin), user_id) == 0
    finally:
        await admin.dispose()
    wiped = {e["user_id"]: e for e in await events(env)}
    assert wiped[alice] == {
        "user_id": alice,
        "providers": ["mowl"],
        "totp_reset": False,
        "mailed": True,
    }
    assert wiped[bob] == {"user_id": bob, "providers": ["mowl"], "totp_reset": True, "mailed": True}
    assert wiped[dave]["providers"] == []
    assert carol not in wiped
    assert not smtp.to("c@example.com")
    [alice_mail] = smtp.to("a@example.com")
    assert "Intelligent Cycling (MOWL)" in alice_mail.text
    assert "#token=" not in alice_mail.text  # no TOTP was reset: no link
    assert TOKENS["a@example.com"] not in alice_mail.text
    [bob_mail] = smtp.to("b@example.com")
    assert "/reset#token=" in bob_mail.text
    assert str(bob) not in bob_mail.text

    # A fresh connect works under the new KEK; web would record its check row on start.
    ring, engine = await ring_for(wipe_settings("kek-3"))
    try:
        await connect(
            ring_factory := create_session_factory(engine), ring, alice, TOKENS["a@example.com"]
        )
        assert await token_of(ring_factory, ring, alice) == TOKENS["a@example.com"]
    finally:
        await engine.dispose()


async def test_wipe_detaches_saved_workouts_and_the_class_shows_the_count(
    env: Env, app_database_url: str, owner_database_url: str, kek_dir: Path, wipe_settings: Any
) -> None:
    """ADR 0007 sections 9 and 12: ``keys wipe`` detaches the user's live saved workouts like a
    disconnect does, so the class API shows ``detached_count`` and no live row; another user's
    rows are untouched."""
    alice = await env.user("a@example.com")
    bob = await env.user("b@example.com")
    for email, user_id in (("a@example.com", alice), ("b@example.com", bob)):
        await connect(env.factory, env.app.state.keyring, user_id, TOKENS[email])
    classes: dict[UUID, UUID] = {}
    admin = create_migrations_engine(wipe_settings())
    try:
        for number, user_id in enumerate((alice, bob), start=1):
            async with user_session(create_session_factory(admin), make_context(user_id)) as db:
                classes[user_id] = uuid.uuid4()
                await db.execute(
                    text("INSERT INTO classes (id, owner_id, title) VALUES (:c, :o, 'Ride')"),
                    {"c": classes[user_id], "o": user_id},
                )
                for extra in range(2):
                    await db.execute(
                        text(
                            "INSERT INTO saved_workouts (id, owner_id, class_id, program_id, "
                            "revision_number, content_hash, name) VALUES (:i, :o, :c, :p, 1, "
                            "decode(repeat('ab', 32), 'hex'), 'Ride')"
                        ),
                        {
                            "i": uuid.uuid4(),
                            "o": user_id,
                            "c": classes[user_id],
                            "p": number * 100 + extra,
                        },
                    )
    finally:
        await admin.dispose()
    (kek_dir / "connection_kek").write_bytes(KEY_3.hex().encode())  # the runbook's new KEK
    await stop_app(env)

    code, _, err = await cli(wipe_settings("kek-3"), "keys", "wipe", "--confirm")
    assert (code, err) == (0, ""), err

    mine = await classes_api.get_class(env.factory, make_context(alice), classes[alice])
    assert mine is not None
    assert (mine.saved_count, mine.detached_count, mine.saved_workout_id) == (0, 2, None)
    both = await classes_api.get_class(env.factory, make_context(bob), classes[bob])
    # Every account is wiped, so bob's rows are detached too; each count is per owner.
    assert both is not None
    assert (both.saved_count, both.detached_count) == (0, 2)
    foreign = await classes_api.get_class(env.factory, make_context(alice), classes[bob])
    assert foreign is None  # and alice never sees bob's class


async def test_wipe_reconnect_wipe_again(
    env: Env, kek_dir: Path, wipe_settings: Any, smtp: SmtpFake
) -> None:
    alice = await env.user("a@example.com")
    await connect(env.factory, env.app.state.keyring, alice, TOKENS["a@example.com"])
    (kek_dir / "connection_kek").write_bytes(KEY_2.hex().encode())
    await stop_app(env)
    assert (await cli(wipe_settings("kek-2"), "keys", "wipe", "--confirm"))[0] == 0
    # Back in service under kek-2, Alice reconnects; later kek-2 leaks as well.
    ring, engine = await ring_for(wipe_settings("kek-2"))
    try:
        await connect(create_session_factory(engine), ring, alice, TOKENS["b@example.com"])
    finally:
        await engine.dispose()
    code, out, _ = await cli(wipe_settings("kek-3"), "keys", "wipe", "--confirm")
    assert code == 0
    assert "wiped: 1" in out
    assert [e["providers"] for e in await events(env)] == [["mowl"], ["mowl"]]
    assert len(smtp.to("a@example.com")) == 2
    # And a third run finds nothing to wipe: no event, no mail.
    await stop_app(env)
    code, out, _ = await cli(wipe_settings("kek-4"), "keys", "wipe", "--confirm")
    assert (code, "nothing to wipe: 1" in out) == (0, True)
    assert len(smtp.to("a@example.com")) == 2


async def test_unsent_mails_are_counted_and_resent_from_the_database(
    env: Env, wipe_settings: Any, smtp: SmtpFake, unreachable_database_url: str
) -> None:
    alice = await env.user("a@example.com")
    bob = await env.user("b@example.com")
    await connect(env.factory, env.app.state.keyring, alice, TOKENS["a@example.com"])
    await add_totp(env, env.app.state.keyring, bob)
    await stop_app(env)
    closed_port = int(unreachable_database_url.rsplit(":", 1)[1].split("/")[0])
    code, out, _ = await cli(wipe_settings(smtp_port=closed_port), "keys", "wipe", "--confirm")
    assert code == 0  # an unsent mail never blocks the wipe
    assert "mails unsent: 2" in out
    assert "keys wipe --resend" in out
    assert [e.get("mailed") for e in await events(env)] == [None, None]

    # An earlier reset link of Bob's stops working once --resend issues a fresh one.
    await env.run(
        "INSERT INTO auth_tokens (purpose, token_hash, user_id, expires_at) "
        "VALUES ('reset_password', '\\x01', :u, now() + interval '1 hour')",
        u=bob,
    )
    # --resend skips both refusals: the app may be running again (and kek-3 has its row).
    await env.scalar("SELECT 1")
    code, out, _ = await cli(
        wipe_settings("kek-1", smtp_port=closed_port), "keys", "wipe", "--resend"
    )
    assert (code, out) == (1, "wipe mails: 0 sent, 2 unsent\n")
    code, out, _ = await cli(wipe_settings("kek-1"), "keys", "wipe", "--resend")
    assert (code, out) == (0, "wipe mails: 2 sent, 0 unsent\n")
    assert [e.get("mailed") for e in await events(env)] == [True, True]
    hashes: Any = await env.scalar(
        "SELECT array_agg(token_hash) FROM auth_tokens WHERE user_id = :u "
        "AND purpose = 'reset_password'",
        u=bob,
    )
    assert len(hashes) == 1  # one valid link at a time
    assert bytes(hashes[0]) != b"\x01"  # the earlier one is gone
    [bob_mail] = smtp.to("b@example.com")
    assert "/reset#token=" in bob_mail.text
    code, out, _ = await cli(wipe_settings("kek-1"), "keys", "wipe", "--resend")
    assert (code, out) == (0, "wipe mails: 0 sent, 0 unsent\n")


async def test_resend_survives_a_failure_other_than_a_mail_error(
    env: Env,
    wipe_settings: Any,
    smtp: SmtpFake,
    unreachable_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#139 item 3: one mail that fails with something else than ``MailError`` is counted unsent
    and the other users' mails still go out."""
    from classbeat import keys_cli

    alice = await env.user("a@example.com")
    bob = await env.user("b@example.com")
    for email, user_id in (("a@example.com", alice), ("b@example.com", bob)):
        await connect(env.factory, env.app.state.keyring, user_id, TOKENS[email])
    await stop_app(env)
    closed_port = int(unreachable_database_url.rsplit(":", 1)[1].split("/")[0])
    code, out, _ = await cli(wipe_settings(smtp_port=closed_port), "keys", "wipe", "--confirm")
    assert (code, "mails unsent: 2" in out) == (0, True)
    real = keys_cli.auth.wipe_mail

    async def flaky(settings: Any, db: Any, event: Any) -> Any:
        if event.user_id == alice:
            raise ValueError("not a mail error")
        return await real(settings, db, event)

    monkeypatch.setattr(keys_cli.auth, "wipe_mail", flaky)
    code, out, _ = await cli(wipe_settings("kek-1"), "keys", "wipe", "--resend")
    assert (code, out) == (1, "wipe mails: 1 sent, 1 unsent\n")
    assert (len(smtp.to("a@example.com")), len(smtp.to("b@example.com"))) == (0, 1)
    assert sorted(e.get("mailed") is True for e in await events(env)) == [False, True]


async def test_the_owner_role_without_a_context_sees_no_connection_rows(
    env: Env, wipe_settings: Any
) -> None:
    alice = await env.user("a@example.com")
    await connect(env.factory, env.app.state.keyring, alice, TOKENS["a@example.com"])
    engine = create_migrations_engine(wipe_settings())
    try:
        factory = create_session_factory(engine)
        async with system_session(factory) as db:  # app.user_id empty for this transaction
            assert (await db.execute(text("SELECT count(*) FROM connections"))).scalar() == 0
        assert await owner_count(factory, alice) == 1  # SET LOCAL per transaction
        async with factory() as session:  # a later transaction on the pooled connection
            setting = await session.execute(text("SELECT current_setting('app.user_id', true)"))
            assert setting.scalar() in (None, "")
            assert (await session.execute(text("SELECT count(*) FROM connections"))).scalar() == 0
    finally:
        await engine.dispose()


async def test_a_failing_account_does_not_stop_the_others_and_is_reported(
    env: Env, wipe_settings: Any, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    from classbeat import keys_cli

    alice = await env.user("a@example.com")
    bob = await env.user("b@example.com")
    for email, user_id in (("a@example.com", alice), ("b@example.com", bob)):
        await connect(env.factory, env.app.state.keyring, user_id, TOKENS[email])
    await stop_app(env)
    real = keys_cli.auth.wipe_user

    async def flaky(db: Any, user_id: Any, providers: Any) -> Any:
        if user_id == alice:
            raise ValueError("not a database error: row values must not be printed")
        return await real(db, user_id, providers)

    monkeypatch.setattr(keys_cli.auth, "wipe_user", flaky)
    code, out, err = await cli(wipe_settings(), "keys", "wipe", "--confirm")
    assert code == 1
    assert "failed: 1" in out
    assert "wiped: 1" in out  # Bob's account was still processed
    assert "1 accounts were not processed (ValueError: 1)" in err
    assert "row values" not in err + out
    assert "KEK check rows deleted" not in out  # nothing is forgotten while an account failed
    assert await owner_count_of(wipe_settings(), bob) == 0
    assert await owner_count_of(wipe_settings(), alice) == 1  # rolled back, to be run again
    assert len(smtp.to("b@example.com")) == 1
    assert smtp.to("a@example.com") == []


async def owner_count_of(settings: Any, user_id: Any) -> int:
    admin = create_migrations_engine(settings)
    try:
        return await owner_count(create_session_factory(admin), user_id)
    finally:
        await admin.dispose()
