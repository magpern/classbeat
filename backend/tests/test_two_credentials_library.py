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


def test_concurrent_credentials_carry_only_their_own_token(two_mowl: TwoMowl) -> None:
    mine = {a.label: Credentials(mowl_token=a.token) for a in ACCOUNTS}

    def work(label: str) -> list[str]:
        return [w["Name"] for _ in range(ROUNDS) for w in library_api.list_workouts(mine[label])]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {label: [pool.submit(work, label) for _ in range(2)] for label in mine}
        results = {label: [f.result() for f in fs] for label, fs in futures.items()}
    for label, per_thread in results.items():
        assert per_thread == [[f"{label} workout"] * ROUNDS] * 2
    counts = {k: n for k, n in two_mowl.counts().items()}
    assert counts == {("programs", a.label, True): 2 * ROUNDS for a in ACCOUNTS}


def test_a_token_that_crossed_would_be_seen(two_mowl: TwoMowl) -> None:
    """The check has teeth: swap the tokens in a test double and the same assertions fail."""
    swapped = {ALICE.label: Credentials(mowl_token=BOB.token)}  # Alice holding Bob's token
    seen = [w["Name"] for w in library_api.list_workouts(swapped[ALICE.label])]
    with pytest.raises(AssertionError):
        assert seen == [f"{ALICE.label} workout"]


def test_a_missing_token_never_borrows_another(two_mowl: TwoMowl) -> None:
    with pytest.raises(MowlError):
        library_api.list_workouts(Credentials())  # no token: refused locally, no call made
    assert two_mowl.calls == []
