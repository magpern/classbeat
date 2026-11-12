# ruff: noqa: F811 - the fixtures are imported from test_paddle_webhook and named as parameters
"""The fake hosted checkout page (PB-3, ADR 0032 section 7): the page the fake Paddle sends a buyer
to, its two buttons and the signed delivery they trigger, and the proof that it exists only for
`fake_paddle` and never in prod. No network: the page, script and delivery are in process."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from test_billing_routes import make_tester, sub
from test_paddle_webhook import (  # noqa: F401 - the fixtures: env, tester
    BILLING,
    checkout,
    env,
    paddle,
    tester,
)

ACTION = "/api/billing/paddle/fake/{ref}/{outcome}"


def act(actor: Any, ref: str, outcome: str) -> Any:
    return actor.call("POST", ACTION.format(ref=ref, outcome=outcome))


# --- the page -----------------------------------------------------------------------------------


async def test_the_page_offers_pay_and_decline_for_an_open_checkout(
    env: Env, tester: Any, priced: None
) -> None:
    ref = await checkout(env, tester)
    page = await tester.get(f"/fake-paddle/checkout/{ref}")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    body = page.text
    assert "Fake Paddle checkout" in body
    assert 'data-outcome="pay"' in body
    assert 'data-outcome="fail"' in body
    assert f'data-ref="{ref}"' in body
    assert "<script>" not in body  # the script is a file: the CSP allows 'self' only
    script = await tester.get("/fake-paddle/checkout.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "/api/billing/paddle/fake/" in script.text


async def test_an_unknown_or_malformed_transaction_has_no_page(env: Env, tester: Any) -> None:
    for ref in ("txn_" + "0" * 26, "txn_x", "nothing", "txn_" + "0" * 26 + "<b>"):
        assert (await tester.get(f"/fake-paddle/checkout/{ref}")).status_code == 404, ref


# --- paying and declining -------------------------------------------------------------------------


async def test_paying_delivers_the_signed_notifications_and_the_plan_starts(
    env: Env, tester: Any, superuser: Connection, priced: None
) -> None:
    ref = await checkout(env, tester)
    assert sub(superuser, tester.user_id) is None  # nothing until Paddle's notification lands
    answer = await act(tester, ref, "pay")
    assert (answer.status_code, answer.json()) == (200, {"outcome": "pay", "undelivered": 0})
    state = sub(superuser, tester.user_id)
    assert (state.plan_id, state.status) == ("instructor", "active")
    provider = superuser.execute(
        text("SELECT provider FROM subscriptions WHERE owner_id = :o"), {"o": tester.user_id}
    ).scalar_one()
    assert provider == "fake_paddle"
    assert paddle(env).outbox == []
    seen = (await tester.get(BILLING)).json()["subscription"]
    assert (seen["plan"], seen["provider"]) == ("instructor", "fake_paddle")
    assert seen["status"] == "active"
    assert (await tester.get(f"/fake-paddle/checkout/{ref}")).status_code == 404  # paid
    assert (await act(tester, ref, "pay")).status_code == 404  # never twice


async def test_declining_leaves_the_plan_and_the_page_open_to_pay_after_all(
    env: Env, tester: Any, superuser: Connection, priced: None
) -> None:
    ref = await checkout(env, tester)
    answer = await act(tester, ref, "fail")
    assert (answer.status_code, answer.json()) == (200, {"outcome": "fail", "undelivered": 0})
    assert sub(superuser, tester.user_id) is None
    assert (await tester.get(BILLING)).json()["subscription"] is None
    assert (await tester.get(f"/fake-paddle/checkout/{ref}")).status_code == 200  # a retry
    assert (await act(tester, ref, "pay")).status_code == 200
    assert sub(superuser, tester.user_id).plan_id == "instructor"


async def test_only_the_buyer_acts_on_their_checkout(
    env: Env, tester: Any, make_actor: Any, superuser: Connection, priced: None
) -> None:
    ref = await checkout(env, tester)
    other = await make_tester(env, make_actor, superuser, "other@example.com")
    assert (await act(other, ref, "pay")).status_code == 404
    assert (await act(other, ref, "fail")).status_code == 404
    assert sub(superuser, tester.user_id) is None
    assert sub(superuser, other.user_id) is None
    anonymous = await env.client.post(ACTION.format(ref=ref, outcome="pay"))
    assert anonymous.status_code in (401, 403)
    assert paddle(env).checkout_of(ref) is not None  # still waiting for its buyer


async def test_an_outcome_other_than_pay_or_fail_is_refused(
    env: Env, tester: Any, priced: None
) -> None:
    ref = await checkout(env, tester)
    assert (await act(tester, ref, "refund")).status_code == 422
    assert paddle(env).checkout_of(ref) is not None


# --- never outside the fake ---------------------------------------------------------------------


@pytest.fixture
async def dummy_env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as dummy:
        yield dummy


async def test_with_the_dummy_provider_nothing_of_it_is_mounted(dummy_env: Env) -> None:
    ref = "txn_" + "0" * 26
    assert getattr(dummy_env.app.state, "paddle_client", None) is None
    mounted = [getattr(r, "path", "") for r in dummy_env.app.routes]
    assert [path for path in mounted if "fake" in path] == []  # no page, script or action
    for path in (f"/fake-paddle/checkout/{ref}", "/fake-paddle/checkout.js"):
        assert (await dummy_env.client.get(path)).status_code == 404, path


async def test_in_prod_every_handler_refuses_even_if_it_were_mounted(
    env: Env, tester: Any, priced: None
) -> None:
    """The settings refuse `fake_paddle` in prod (`test_paddle_settings`) and `billing.setup` does
    not mount it there; this is the third line: each handler answers 404 on its own."""
    ref = await checkout(env, tester)
    env.app.state.settings = env.app.state.settings.model_copy(update={"environment": "prod"})
    for path in (f"/fake-paddle/checkout/{ref}", "/fake-paddle/checkout.js"):
        assert (await tester.get(path)).status_code == 404, path
    assert (await act(tester, ref, "pay")).status_code == 404
    assert paddle(env).checkout_of(ref) is not None  # nothing was paid
