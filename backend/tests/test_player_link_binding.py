"""The link is kept only for classbeat's issuer and the user's own pairwise subject (ADR 0034
section 3, #1059 criterion 2). Any other ``account_subject`` (another instructor's, another
issuer's, none at all) or a non-Bearer token type ends the link at once with
``DELETE /api/source/link`` and stores nothing; a reconnect over such an answer keeps the old
row."""

import uuid
from typing import Any

import pytest

from oidctools import expected_subject
from playertools import Linker


async def _refused(linker: Linker, subject: Any) -> None:
    alice = await linker.actor("alice@example.com")
    url = await linker.start(alice)
    linker.fake.subject = subject(linker, alice)
    assert await linker.callback(alice, linker.fake.approve(url)) == {"result": "wrong_account"}
    assert linker.fake.ended == linker.fake.tokens
    assert len(linker.fake.tokens) == 1
    assert await linker.connection(linker.user_id(alice)) is None
    assert await linker.flows(linker.user_id(alice)) == []


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param(
            lambda linker, _: {
                "iss": linker.issuer,
                "sub": expected_subject(uuid.uuid4()),
            },
            id="another-instructor",
        ),
        pytest.param(
            lambda linker, actor: {"iss": "https://other.example", "sub": linker.subject(actor)},
            id="another-issuer",
        ),
        pytest.param(
            lambda linker, actor: {"iss": linker.issuer + "/", "sub": linker.subject(actor)},
            id="issuer-with-slash",
        ),
        pytest.param(lambda linker, actor: {"sub": linker.subject(actor)}, id="no-issuer"),
        pytest.param(lambda linker, actor: "omit", id="no-account-subject"),
        pytest.param(lambda linker, actor: [linker.issuer], id="not-an-object"),
    ],
)
async def test_a_foreign_account_subject_ends_the_link_and_stores_nothing(
    linker: Linker, subject: Any
) -> None:
    await _refused(linker, subject)


async def test_another_real_users_subject_is_refused(linker: Linker) -> None:
    bob = await linker.actor("bob@example.com")
    bob_subject = linker.subject(bob)
    await _refused(linker, lambda linker, _: {"iss": linker.issuer, "sub": bob_subject})
    assert await linker.connection(linker.user_id(bob)) is None


async def test_a_mismatch_on_reconnect_keeps_the_old_link(linker: Linker) -> None:
    alice = await linker.actor("alice@example.com")
    await linker.connect(alice)
    before = await linker.connection(linker.user_id(alice))
    old = linker.fake.tokens[0]
    url = await linker.start(alice)
    linker.fake.subject = {"iss": linker.issuer, "sub": "someone-else"}
    assert await linker.callback(alice, linker.fake.approve(url)) == {"result": "wrong_account"}
    assert linker.fake.ended == [linker.fake.tokens[1]]
    assert old not in linker.fake.ended
    after = await linker.connection(linker.user_id(alice))
    assert (after.id, after.generation, bytes(after.secret)) == (
        before.id,
        before.generation,
        bytes(before.secret),
    )


@pytest.mark.parametrize(
    ("status", "category"),
    [(400, "access_denied"), (429, "rate_limited"), (503, "upstream_unavailable")],
)
async def test_a_refused_exchange_stores_nothing(
    linker: Linker, status: int, category: str
) -> None:
    alice = await linker.actor("alice@example.com")
    url = await linker.start(alice)
    linker.fake.exchange_status = status
    assert await linker.callback(alice, linker.fake.approve(url)) == {"result": category}
    assert await linker.connection(linker.user_id(alice)) is None
    assert linker.fake.ended == []
