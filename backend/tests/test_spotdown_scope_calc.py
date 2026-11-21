"""``cache_scope`` and ``Idempotency-Key`` (ADR 0040 section 7, ``classbeat.spotdown.scope_calc``):
an HMAC of the owner id, never the raw id; stable per owner and key; distinct across owners and
keys; inside the contract's pattern."""

import base64
import hashlib
import hmac
import uuid

import pytest

from classbeat.spotdown.scope_calc import (
    IDEMPOTENCY_PATTERN,
    SCOPE_PATTERN,
    cache_scope,
    idempotency_key,
    shared_scope,
)

KEY = bytes(range(32))
OWNER = uuid.UUID("2f1d7c9e-3a4b-4c5d-8e6f-0123456789ab")


def test_the_scope_is_an_hmac_of_the_owner_id_and_never_the_id() -> None:
    scope = cache_scope(KEY, OWNER)
    assert SCOPE_PATTERN.fullmatch(scope)
    assert scope.startswith("o1-")
    assert len(scope) == 46
    for form in (str(OWNER), OWNER.hex, OWNER.hex[:8], str(OWNER.int)):
        assert form not in scope
    expected = hmac.new(
        KEY, b"classbeat/spotdown-scope/v1\x00" + OWNER.bytes, hashlib.sha256
    ).digest()
    assert scope != hashlib.sha256(OWNER.bytes).hexdigest()
    assert scope == "o1-" + base64.urlsafe_b64encode(expected).rstrip(b"=").decode()


def test_the_scope_is_stable_and_differs_across_owners_and_keys() -> None:
    other = uuid.UUID(int=OWNER.int ^ 1)
    assert cache_scope(KEY, OWNER) == cache_scope(KEY, OWNER)
    assert cache_scope(KEY, OWNER) != cache_scope(KEY, other)
    assert cache_scope(KEY, OWNER) != cache_scope(bytes(32), OWNER)


def test_the_scope_needs_a_uuid_and_a_long_key() -> None:
    with pytest.raises(TypeError):
        cache_scope(KEY, str(OWNER))  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="at least 32"):
        cache_scope(b"short", OWNER)


def test_a_shared_scope_names_no_owner_and_is_never_an_owners() -> None:
    scope = shared_scope(KEY, "studio")
    assert SCOPE_PATTERN.fullmatch(scope)
    assert scope.startswith("s1-")
    assert len(scope) == 46
    expected = hmac.new(
        KEY, b"classbeat/spotdown-shared-scope/v1\x00studio", hashlib.sha256
    ).digest()
    assert scope == "s1-" + base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    assert scope == shared_scope(KEY, "studio")
    assert scope != shared_scope(KEY, "mix")
    assert scope != shared_scope(bytes(32), "studio")
    assert scope.removeprefix("s1-") != cache_scope(KEY, OWNER).removeprefix("o1-")


@pytest.mark.parametrize("name", ["", "Studio", "studio-1", "a" * 33, "st\x00"])
def test_a_shared_scope_needs_a_lowercase_name_and_a_long_key(name: str) -> None:
    with pytest.raises(ValueError, match="lowercase letters"):
        shared_scope(KEY, name)
    with pytest.raises(ValueError, match="at least 32"):
        shared_scope(b"short", "studio")


def test_the_idempotency_key_is_stable_hashed_and_distinct() -> None:
    key = idempotency_key("job-1", "analyse", "track-7")
    assert IDEMPOTENCY_PATTERN.fullmatch(key)
    assert key == idempotency_key("job-1", "analyse", "track-7")
    assert key != idempotency_key("job-1", "analyse", "track-8")
    assert key != idempotency_key("job-1analyse", "track-7")  # the parts are separated
    assert "job-1" not in key


@pytest.mark.parametrize("parts", [(), ("",), ("a", ""), ("a\x00b",)])
def test_bad_idempotency_parts_are_refused(parts: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="non-empty parts"):
        idempotency_key(*parts)
