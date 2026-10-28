"""``classbeat.crypto.envelope``: the pure envelope primitive (ADR 0004 section 2)."""

import pickle
import uuid

import pytest

from classbeat.crypto.envelope import (
    CHECK_VALUE_LENGTH,
    DecryptFailed,
    Kek,
    canonical_json,
    check_value,
    connection_aad,
    connection_wrap_aad,
    is_key_id,
    open_sealed,
    rewrap,
    seal,
    totp_aad,
    totp_wrap_aad,
    unwrap,
)

KEK = Kek("kek-1", bytes(range(32)))
OTHER_KEK = Kek("kek-2", bytes(range(1, 33)))
OWNER, ROW = uuid.uuid4(), uuid.uuid4()
PAYLOAD = canonical_json({"token": "11111111-2222-3333-4444-555555555555"})


def sealed_for(owner: uuid.UUID = OWNER, provider: str = "mowl", row: uuid.UUID = ROW):
    return seal(
        PAYLOAD,
        connection_aad(owner, provider, row),
        KEK,
        connection_wrap_aad(KEK.key_id, owner, provider, row),
    )


def open_for(
    sealed, owner=OWNER, provider="mowl", row=ROW, kek: Kek = KEK, key_id: str | None = None
) -> bytes:
    return open_sealed(
        sealed.ciphertext,
        sealed.nonce,
        sealed.wrapped_dek,
        sealed.dek_nonce,
        kek,
        connection_aad(owner, provider, row),
        connection_wrap_aad(key_id or kek.key_id, owner, provider, row),
    )


def test_round_trip() -> None:
    sealed = sealed_for()
    assert open_for(sealed) == PAYLOAD
    assert sealed.key_id == "kek-1"
    assert (len(sealed.nonce), len(sealed.dek_nonce), len(sealed.wrapped_dek)) == (12, 12, 48)
    assert PAYLOAD not in sealed.ciphertext
    assert PAYLOAD not in sealed.wrapped_dek


def test_every_write_has_a_new_data_key_and_nonces() -> None:
    first, second = sealed_for(), sealed_for()
    assert first.ciphertext != second.ciphertext
    assert first.nonce != second.nonce
    assert first.wrapped_dek != second.wrapped_dek
    assert first.dek_nonce != second.dek_nonce


@pytest.mark.parametrize(
    "change",
    [
        {"owner": uuid.uuid4()},  # another user's row
        {"provider": "spotify"},  # another provider's row
        {"row": uuid.uuid4()},  # another row of the same user
        {"kek": OTHER_KEK},  # another KEK
        {"key_id": "kek-9"},  # the right key under another id (wrap AAD)
    ],
)
def test_wrong_aad_or_key_fails(change: dict[str, object]) -> None:
    with pytest.raises(DecryptFailed) as raised:
        open_for(sealed_for(), **change)  # pyright: ignore[reportArgumentType]
    assert raised.value.args == ("decrypt_failed",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "wrapped_dek", "dek_nonce"])
def test_tampered_bytes_fail(field: str) -> None:
    sealed = sealed_for()
    value = bytearray(getattr(sealed, field))
    value[0] ^= 1
    parts = {
        name: bytes(value) if name == field else getattr(sealed, name)
        for name in ("ciphertext", "nonce", "wrapped_dek", "dek_nonce")
    }
    with pytest.raises(DecryptFailed):
        open_sealed(
            parts["ciphertext"],
            parts["nonce"],
            parts["wrapped_dek"],
            parts["dek_nonce"],
            KEK,
            connection_aad(OWNER, "mowl", ROW),
            connection_wrap_aad(KEK.key_id, OWNER, "mowl", ROW),
        )


def test_a_truncated_nonce_fails_closed() -> None:
    sealed = sealed_for()
    with pytest.raises(DecryptFailed):
        unwrap(sealed.wrapped_dek, b"", KEK, connection_wrap_aad("kek-1", OWNER, "mowl", ROW))


def test_rewrap_keeps_the_data_key_and_the_ciphertext() -> None:
    sealed = sealed_for()
    wrapped, nonce = rewrap(
        sealed.wrapped_dek,
        sealed.dek_nonce,
        KEK,
        connection_wrap_aad("kek-1", OWNER, "mowl", ROW),
        OTHER_KEK,
        connection_wrap_aad("kek-2", OWNER, "mowl", ROW),
    )
    assert wrapped != sealed.wrapped_dek
    moved = type(sealed)(sealed.ciphertext, sealed.nonce, wrapped, nonce, "kek-2")
    assert open_for(moved, kek=OTHER_KEK) == PAYLOAD
    with pytest.raises(DecryptFailed):
        open_for(moved, kek=KEK, key_id="kek-2")  # the old KEK no longer opens the new wrap
    # The old wrap still opens with the old KEK: rewrap is no response to a leaked key.
    assert open_for(sealed) == PAYLOAD


def test_totp_aad_binds_the_user_and_the_key_id() -> None:
    user = uuid.uuid4()
    sealed = seal(b"JBSWY3DPEHPK3PXP", totp_aad(user), KEK, totp_wrap_aad("kek-1", user))
    args = (sealed.ciphertext, sealed.nonce, sealed.wrapped_dek, sealed.dek_nonce, KEK)
    assert open_sealed(*args, totp_aad(user), totp_wrap_aad("kek-1", user)) == b"JBSWY3DPEHPK3PXP"
    other = uuid.uuid4()
    with pytest.raises(DecryptFailed):
        open_sealed(*args, totp_aad(other), totp_wrap_aad("kek-1", other))
    # A connection payload's AAD never opens a TOTP secret, and the other way round.
    with pytest.raises(DecryptFailed):
        open_sealed(*args, connection_aad(user, "mowl", user), totp_wrap_aad("kek-1", user))


def test_the_aad_strings_are_exactly_the_adr_forms() -> None:
    owner = uuid.UUID("0f8e0d6c-1d4a-4c5e-9b1e-2a3b4c5d6e7f")
    row = uuid.UUID("11111111-2222-3333-4444-555555555555")
    assert connection_aad(owner, "mowl", row) == (
        b"classbeat-conn-v1|0f8e0d6c-1d4a-4c5e-9b1e-2a3b4c5d6e7f|mowl|"
        b"11111111-2222-3333-4444-555555555555"
    )
    assert connection_wrap_aad("kek-1", owner, "spotify", row) == (
        b"classbeat-dek-v1|kek-1|0f8e0d6c-1d4a-4c5e-9b1e-2a3b4c5d6e7f|spotify|"
        b"11111111-2222-3333-4444-555555555555"
    )
    assert totp_aad(owner) == b"classbeat-totp-v2|0f8e0d6c-1d4a-4c5e-9b1e-2a3b4c5d6e7f"
    assert totp_wrap_aad("kek-a", owner) == (
        b"classbeat-totp-dek-v1|kek-a|0f8e0d6c-1d4a-4c5e-9b1e-2a3b4c5d6e7f"
    )


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda: connection_aad(OWNER, "mowl|x", ROW), ValueError),
        (lambda: connection_aad(str(OWNER), "mowl", ROW), TypeError),  # pyright: ignore[reportArgumentType]
        (lambda: connection_wrap_aad("kek-1|x", OWNER, "mowl", ROW), ValueError),
        (lambda: totp_wrap_aad("app-v1", OWNER), ValueError),
    ],
)
def test_aad_fields_cannot_carry_a_separator(call, error) -> None:
    with pytest.raises(error):
        call()


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("kek-1", True),
        ("kek-2026-10-a", True),
        ("kek-" + "a" * 28, True),
        ("kek-" + "a" * 29, False),
        ("kek-", False),
        ("KEK-1", False),
        ("kek_1", False),
        ("app-v1", False),
        ("kek-1\n", False),
        ("../kek-1", False),
        (None, False),
    ],
)
def test_the_key_id_pattern(value: object, ok: bool) -> None:
    assert is_key_id(value) is ok


def test_a_kek_hides_its_key_and_cannot_be_pickled() -> None:
    assert repr(KEK) == "Kek(key_id='kek-1', key=...)"
    assert bytes(range(32)).hex() not in repr(KEK)
    with pytest.raises(TypeError):
        pickle.dumps(KEK)
    with pytest.raises(ValueError, match="32 bytes"):
        Kek("kek-1", b"short")
    with pytest.raises(ValueError, match="does not match"):
        Kek("bad", bytes(32))
    assert "ciphertext" not in repr(sealed_for())


def test_check_value_and_canonical_json() -> None:
    assert len(check_value(KEK.key)) == CHECK_VALUE_LENGTH
    assert check_value(KEK.key) != check_value(OTHER_KEK.key)
    assert canonical_json({"b": 1, "a": "ü"}) == '{"a":"ü","b":1}'.encode()
