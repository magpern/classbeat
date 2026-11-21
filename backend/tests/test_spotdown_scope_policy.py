"""Which ``cache_scope`` a source kind uses (``classbeat.spotdown.scope_policy``, ADR 0040
section 7, amendment of 2026-11-21, #1338): an allowlist. Only ``studio`` shares one scope across
owners; a mix and an upload keep the owner's; any other kind is refused. That no upload, sha256
lookup or upload analysis leaves without a scope is ``test_spotdown_client``'s."""

import uuid

import pytest

from classbeat.spotdown.scope_calc import cache_scope, shared_scope
from classbeat.spotdown.scope_policy import OWNER_KINDS, SHARED_KINDS, ScopeRefused, scope_for

KEY = bytes(range(32))
OWNER = uuid.UUID("2f1d7c9e-3a4b-4c5d-8e6f-0123456789ab")
OTHER = uuid.UUID(int=OWNER.int ^ 1)


def test_the_allowlist_shares_only_the_studio() -> None:
    assert frozenset({"studio"}) == SHARED_KINDS
    assert frozenset({"mix", "upload"}) == OWNER_KINDS
    assert not SHARED_KINDS & OWNER_KINDS


@pytest.mark.parametrize(("kind", "shared"), [("studio", True), ("mix", False), ("upload", False)])
def test_each_kind_gets_its_scope(kind: str, shared: bool) -> None:
    mine, theirs = scope_for(kind, KEY, OWNER), scope_for(kind, KEY, OTHER)
    assert (mine == theirs) is shared
    if shared:
        assert mine == shared_scope(KEY, kind)
        assert mine.startswith("s1-")
        assert cache_scope(KEY, OWNER) != mine
    else:
        assert mine == cache_scope(KEY, OWNER)
        assert theirs == cache_scope(KEY, OTHER)


@pytest.mark.parametrize("kind", ["", "Studio", "studios", "youtube", "url", "spotify", "x"])
def test_an_unknown_kind_is_refused(kind: str) -> None:
    with pytest.raises(ScopeRefused) as refused:
        scope_for(kind, KEY, OWNER)
    assert str(refused.value) == "a source kind the scope policy does not name"  # no value
