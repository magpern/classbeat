"""Migration 0041: the passkey tables' rules (ADR 0019 sections 6 and 7, W1 #771).

The generic checks (system tables without RLS, the app role's DML, the columns matching
``classbeat.auth.tables``, the cascades) are in ``test_auth_schema.py``; here the constraints that
keep a credential, a challenge and the user handle well formed.
"""

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

H32 = hashlib.sha256(b"x").digest()


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("TRUNCATE users, webauthn_challenges CASCADE"))
        connection.commit()
        yield connection
        connection.rollback()
    engine.dispose()


def make_user(admin: Connection, email: str = "a@x.se") -> Any:
    return admin.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at) "
            "VALUES (:e, :e, now()) RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def make_session(admin: Connection, user: Any) -> Any:
    return admin.execute(
        text(
            "INSERT INTO sessions (user_id, token_hash, expires_at) "
            "VALUES (:u, :t, now() + interval '1 day') RETURNING id"
        ),
        {"u": user, "t": hashlib.sha256(str(user).encode()).digest()},
    ).scalar_one()


def credential(admin: Connection, user: Any, **values: Any) -> None:
    row = {
        "credential_id": b"\x01" * 16,
        "public_key": b"\xa5\x01\x02",
        "sign_count": 0,
        "transports": ["internal"],
        "backup_eligible": True,
        "backup_state": False,
        "label": "Passkey",
        **values,
    }
    admin.execute(
        text(
            "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, sign_count, "
            "transports, backup_eligible, backup_state, label) VALUES (:u, :credential_id, "
            ":public_key, :sign_count, :transports, :backup_eligible, :backup_state, :label)"
        ),
        {"u": user, **row},
    )


def challenge(admin: Connection, **values: Any) -> None:
    row = {
        "flow_hash": H32,
        "purpose": "sign_in",
        "challenge": H32,
        "user_id": None,
        "session_id": None,
        **values,
    }
    admin.execute(
        text(
            "INSERT INTO webauthn_challenges (flow_hash, purpose, challenge, user_id, session_id, "
            "expires_at) VALUES (:flow_hash, :purpose, :challenge, :user_id, :session_id, "
            "now() + interval '5 minutes')"
        ),
        row,
    )


def refused(admin: Connection, insert: Any, *args: Any, **values: Any) -> None:
    admin.commit()
    with pytest.raises(IntegrityError):
        insert(admin, *args, **values)
    admin.rollback()


def test_a_credential_takes_the_adrs_shape_and_refuses_the_rest(admin: Connection) -> None:
    user = make_user(admin)
    credential(admin, user, transports=["usb", "nfc", "ble", "internal", "hybrid", "smart-card"])
    credential(admin, user, credential_id=b"\x02" * 1023, sign_count=4294967295, label="x" * 64)
    credential(admin, user, credential_id=b"\x03", transports=[], backup_state=True)
    for bad in (
        {"credential_id": b"\x01" * 16},  # the same credential id, also for another user below
        {"credential_id": b""},
        {"credential_id": b"\x04" * 1024},  # WebAuthn's bound is 1023 bytes
        {"public_key": b""},
        {"public_key": b"\x00" * 2049},
        {"sign_count": -1},
        {"sign_count": 4294967296},  # the counter is 32 bits
        {"transports": ["internal", "carrier-pigeon"]},
        {"backup_eligible": False, "backup_state": True},  # BS without BE (WebAuthn's flag rule)
        {"label": ""},
        {"label": "x" * 65},
        {"label": None},
    ):
        refused(admin, credential, user, **{"credential_id": b"\x05", **bad})
    other = make_user(admin, "b@x.se")
    refused(admin, credential, other)  # a credential id is unique across users


def test_a_challenge_is_bound_by_its_purpose(admin: Connection) -> None:
    user = make_user(admin)
    session = make_session(admin, user)
    challenge(admin)
    for purpose in ("register", "reauth"):
        challenge(
            admin,
            flow_hash=hashlib.sha256(purpose.encode()).digest(),
            purpose=purpose,
            user_id=user,
            session_id=session,
        )
    fresh = hashlib.sha256(b"fresh").digest()
    for bad in (
        {"flow_hash": H32},  # the flow hash is unique
        {"purpose": "link"},
        {"flow_hash": b"\x00" * 31},
        {"challenge": b"\x00" * 16},  # 32 random bytes (section 6)
        {"user_id": user, "session_id": session},  # sign_in has no user yet
        {"purpose": "register"},  # register is bound to a user and a session
        {"purpose": "reauth", "user_id": user},  # ... to both
        {"purpose": "reauth", "session_id": session},
    ):
        refused(admin, challenge, **{"flow_hash": fresh, **bad})


def test_the_challenge_goes_with_its_session(admin: Connection) -> None:
    user = make_user(admin)
    session = make_session(admin, user)
    challenge(admin, purpose="register", user_id=user, session_id=session)
    admin.execute(text("DELETE FROM sessions WHERE id = :s"), {"s": session})
    assert admin.execute(text("SELECT count(*) FROM webauthn_challenges")).scalar_one() == 0
    assert admin.execute(text("SELECT count(*) FROM users")).scalar_one() == 1


def test_the_user_handle_is_32_bytes_unique_and_null_until_set(admin: Connection) -> None:
    first, second = make_user(admin), make_user(admin, "b@x.se")
    assert (
        admin.execute(
            text("SELECT webauthn_user_handle FROM users WHERE id = :u"), {"u": first}
        ).scalar_one()
        is None
    )
    set_handle = "UPDATE users SET webauthn_user_handle = :h WHERE id = :u"
    admin.execute(text(set_handle), {"h": H32, "u": first})
    admin.commit()
    for user, handle in ((second, H32), (second, b"\x00" * 16), (second, b"")):
        with pytest.raises(IntegrityError):
            admin.execute(text(set_handle), {"h": handle, "u": user})
        admin.rollback()
