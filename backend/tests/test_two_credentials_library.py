"""Library level (issue #67): two ``mowl`` ``Credentials`` used concurrently never cross.

No database and no app: the vendored library's own calls from several threads at once, against
the fake upstream of ``isofake`` that records the token each call carried and answers with data
of the account it belongs to. The library keeps no token at module level (its ``Credentials``
docstring), and this keeps it so.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from mowl import api as library_api
from mowl.credentials import Credentials
from mowl.errors import MowlError

from isofake import ACCOUNTS, ALICE, BOB, TwoMowl

pytestmark = pytest.mark.usefixtures("two_mowl")
ROUNDS = 25


def run_concurrently(mine: dict[str, Credentials]) -> dict[str, list[list[str]]]:
    """Each label's ``Credentials`` used from two threads, ``ROUNDS`` calls each, interleaved
    with the other label's: what every thread was shown, per label."""

    def work(label: str) -> list[str]:
        return [w["Name"] for _ in range(ROUNDS) for w in library_api.list_workouts(mine[label])]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {label: [pool.submit(work, label) for _ in range(2)] for label in mine}
        return {label: [f.result() for f in fs] for label, fs in futures.items()}


def assert_only_own(results: dict[str, list[list[str]]], two_mowl: TwoMowl) -> None:
    """What each thread was shown is its own account's data, and upstream saw each account's
    token exactly as often as that account's threads called (a crossing breaks one of the two)."""
    for label, per_thread in results.items():
        assert per_thread == [[f"{label} workout"] * ROUNDS] * 2, f"{label} was shown other data"
    counts = dict(two_mowl.counts())
    assert counts == {("programs", a.label, True): 2 * ROUNDS for a in ACCOUNTS}


def test_concurrent_credentials_carry_only_their_own_token(two_mowl: TwoMowl) -> None:
    mine = {a.label: Credentials(mowl_token=a.token) for a in ACCOUNTS}
    assert_only_own(run_concurrently(mine), two_mowl)


def test_the_same_check_fails_when_a_credential_crossed(two_mowl: TwoMowl) -> None:
    """The check has teeth: the same concurrent run with Alice's object holding Bob's token (a
    stand-in for a library that shared state between ``Credentials``) fails ``assert_only_own``,
    on what Alice was shown."""
    crossed = {
        ALICE.label: Credentials(mowl_token=BOB.token),
        BOB.label: Credentials(mowl_token=BOB.token),
    }
    results = run_concurrently(crossed)
    with pytest.raises(AssertionError, match="alice was shown other data"):
        assert_only_own(results, two_mowl)


def test_a_missing_token_never_borrows_another(two_mowl: TwoMowl) -> None:
    with pytest.raises(MowlError):
        library_api.list_workouts(Credentials())  # no token: refused locally, no call made
    assert two_mowl.calls == []
