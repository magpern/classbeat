"""``egress.RequestBudget`` (ADR 0009 section 8, issue #300): every request to a budgeted host
takes one before it is sent, on the worker's urllib opener (a library call, in a thread) and on
``spotify_async_client``; the one over the budget raises ``RequestBudgetExhausted`` and never
reaches the network. Other hosts are not counted. Fakes only: no network."""

import asyncio
import urllib.error
import urllib.request

import pytest

from classbeat import egress
from classbeat.connections.errors import run_library_call
from spotifycore import FakeSpotify
from spotifyfake import CLIENT_A, USER_A, spotify_client, standard, worker_opener

ME = "https://api.spotify.com/v1/me"


def get(opener: urllib.request.OpenerDirector, token: str) -> int:
    request = urllib.request.Request(ME, headers={"Authorization": f"Bearer {token}"})
    with opener.open(request, timeout=5) as answer:
        return answer.status


@pytest.fixture
def fake() -> FakeSpotify:
    return standard()


def test_the_budget_takes_one_per_request_and_refuses_the_next_before_it_is_sent(
    fake: FakeSpotify,
) -> None:
    opener = worker_opener(fake)
    token, _ = fake.grant(CLIENT_A, USER_A)
    budget = egress.RequestBudget(egress.SPOTIFY_HOSTS, limit=2)
    with egress.request_budget(budget):
        assert get(opener, token) == 200
        assert get(opener, token) == 200
        with pytest.raises(egress.RequestBudgetExhausted):
            get(opener, token)
    assert budget.used == 2
    assert len(fake.requests()) == 2  # the third never left
    assert get(opener, token) == 200  # outside the block nothing is counted
    assert budget.used == 2


def test_a_used_budget_starts_where_the_job_left_off(fake: FakeSpotify) -> None:
    opener = worker_opener(fake)
    token, _ = fake.grant(CLIENT_A, USER_A)
    with (
        egress.request_budget(egress.RequestBudget(egress.SPOTIFY_HOSTS, limit=3, used=3)),
        pytest.raises(egress.RequestBudgetExhausted),
    ):
        get(opener, token)
    assert fake.requests() == []


def test_other_hosts_are_not_counted() -> None:
    budget = egress.RequestBudget(egress.SPOTIFY_HOSTS, limit=0)
    budget.take(egress.MOWL_HOST)
    budget.take("API.MOWL.COM")
    assert budget.used == 0
    with pytest.raises(egress.RequestBudgetExhausted):
        budget.take("API.SPOTIFY.COM")


async def test_a_library_call_in_a_thread_is_counted_and_refused_without_its_chain(
    fake: FakeSpotify,
) -> None:
    opener = worker_opener(fake)
    token, _ = fake.grant(CLIENT_A, USER_A)

    def call() -> int:
        try:
            return get(opener, token)
        except egress.RequestBudgetExhausted as exc:
            raise urllib.error.URLError("wrapped by a library") from exc

    budget = egress.RequestBudget(egress.SPOTIFY_HOSTS, limit=1)
    with egress.request_budget(budget):
        assert await run_library_call(call, sign_in=False) == 200
        with pytest.raises(egress.RequestBudgetExhausted) as refused:
            await run_library_call(call, sign_in=False)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert budget.used == 1
    assert len(fake.requests()) == 1


async def test_the_token_client_is_counted_too(fake: FakeSpotify) -> None:
    async with spotify_client(fake) as client:
        budget = egress.RequestBudget(egress.SPOTIFY_HOSTS, limit=1)
        with egress.request_budget(budget):
            await client.get(ME)
            with pytest.raises(egress.RequestBudgetExhausted):
                await client.get(ME)
    assert budget.used == 1
    assert len(fake.requests()) == 1


async def test_concurrent_jobs_each_spend_their_own_budget(fake: FakeSpotify) -> None:
    async with spotify_client(fake) as client:

        async def job(limit: int) -> egress.RequestBudget:
            budget = egress.RequestBudget(egress.SPOTIFY_HOSTS, limit=limit)
            with egress.request_budget(budget):
                for _ in range(limit):
                    await client.get(ME)
                    await asyncio.sleep(0)
            return budget

        one, two = await asyncio.gather(job(2), job(3))
    assert (one.used, two.used) == (2, 3)
    assert len(fake.requests()) == 5
