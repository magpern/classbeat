"""Redeem codes: generation, parsing and hashing (ADR 0011 section 1), shared by redeem (PL1) and
the admin's code creation (AD2).

A code is 16 random bytes written in Crockford base32 (26 characters), shown once. Only the
SHA-256 of the 16 bytes is stored (``redeem_codes.code_hash``), so a code is never compared in
the clear: the user's input is parsed back to its bytes, hashed, and looked up by that hash. The
parse is forgiving the way Crockford's alphabet intends (case, ``-`` and spaces ignored, ``O`` read
as ``0``, ``I`` and ``L`` as ``1``), so every way of writing one code hashes the same. Nothing here
logs or keeps a code.
"""

import hashlib
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_BYTES = 16
CODE_LENGTH = 26  # ceil(128 / 5)
_VALUES = {c: i for i, c in enumerate(ALPHABET)} | {"O": 0, "I": 1, "L": 1}
_IGNORED = str.maketrans("", "", "- \t")


def encode(raw: bytes) -> str:
    """The 26-character Crockford base32 form of 16 bytes."""
    if len(raw) != CODE_BYTES:
        raise ValueError("a code is 16 bytes")
    number = int.from_bytes(raw, "big")
    return "".join(ALPHABET[(number >> (5 * i)) & 31] for i in reversed(range(CODE_LENGTH)))


def parse(code: str) -> bytes | None:
    """The 16 bytes a typed code stands for, or ``None`` when it can't be one."""
    cleaned = code.translate(_IGNORED).upper()
    if len(cleaned) != CODE_LENGTH or not cleaned.isascii():
        return None
    number = 0
    for char in cleaned:
        value = _VALUES.get(char)
        if value is None:
            return None
        number = (number << 5) | value
    if number >> (8 * CODE_BYTES):  # 130 bits written, the top two must be zero
        return None
    return number.to_bytes(CODE_BYTES, "big")


def code_hash(raw: bytes) -> bytes:
    """The stored form: SHA-256 of the code's 16 bytes."""
    if len(raw) != CODE_BYTES:
        raise ValueError("a code is 16 bytes")
    return hashlib.sha256(raw).digest()


def hash_typed(code: str) -> bytes | None:
    """The hash of a typed code, or ``None`` when it can't be one."""
    raw = parse(code)
    return None if raw is None else code_hash(raw)


def new_code() -> tuple[str, bytes]:
    """A fresh code to show once and the hash to store."""
    raw = secrets.token_bytes(CODE_BYTES)
    return encode(raw), code_hash(raw)
