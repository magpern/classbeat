"""The fake hosted checkout page (ADR 0032 section 7, PB-3). Test and compose only.

Where `FakePaddleClient` sends the buyer (`/fake-paddle/checkout/<txn>`): a page with two buttons,
Pay and Decline. A button asks `POST /api/billing/paddle/fake/<txn>/pay|fail`, which has the
fake play Paddle's side (`pay` or `fail`) and delivers its signed notifications to the webhook in
process, so the plan changes only through the same signed path a real Paddle uses. The buyer is
then sent back to Account, Plan with `?checkout=return` or `?checkout=failed`.

**Never in prod.** `billing.setup` mounts this router only with `billing_provider = fake_paddle`,
which the settings refuse in prod; every handler also answers 404 unless the provider is the fake
and the environment is not prod, and the page needs the app's own `FakePaddleClient`. The action
is the signed-in buyer's own (the checkout's owner, CSRF and origin checks as every other POST):
anyone else gets the same 404 as an unknown transaction. No network: the page and its script are
served from this process and the delivery never leaves it.
"""

from __future__ import annotations

import html
import re
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import Request as StarletteRequest

from classbeat import auth
from classbeat.billing import webhook
from classbeat.billing.fake_paddle import CHECKOUT_PATH, FakePaddleClient, Post
from classbeat.billing.paddle_signature import HEADER
from classbeat.context import UserContext

SCRIPT_PATH: Final = "/fake-paddle/checkout.js"
ACTION_PATH: Final = "/api/billing/paddle/fake/{ref}/{outcome}"
_REF: Final = re.compile(r"^txn_[0-9a-f]{26}$")
_ROUNDS: Final = 3  # a first-payment's subscription notice is retried once the payment lands
_NO_STORE: Final = {"Cache-Control": "no-store"}

router = APIRouter(tags=["billing"], include_in_schema=False)

_SCRIPT: Final = """\
const ref = document.body.dataset.ref;
const buttons = document.querySelectorAll('button[data-outcome]');
const note = document.getElementById('note');
const back = { pay: '/account?checkout=return#plan', fail: '/account?checkout=failed#plan' };
const enable = (on) => buttons.forEach((b) => { b.disabled = !on; });
async function act(outcome) {
  enable(false);
  note.textContent = 'Working...';
  try {
    const session = await (await fetch('/api/auth/session', { credentials: 'same-origin' })).json();
    const url = '/api/billing/paddle/fake/' + ref + '/' + outcome;
    const headers = { 'X-CSRF-Token': session.csrf_token };
    const res = await fetch(url, { method: 'POST', credentials: 'same-origin', headers });
    if (!res.ok) throw new Error('status ' + res.status);
    location.assign(back[outcome]);
  } catch (error) {
    note.textContent = 'That did not work: ' + error.message;
    enable(true);
  }
}
buttons.forEach((b) => b.addEventListener('click', () => act(b.dataset.outcome)));
"""


def _fake(request: Request) -> FakePaddleClient:
    """The app's fake client, or 404: the provider is not the fake, or this is prod."""
    settings = request.app.state.settings
    client = getattr(request.app.state, "paddle_client", None)
    if (
        settings.billing_provider != "fake_paddle"
        or settings.environment == "prod"
        or not isinstance(client, FakePaddleClient)
    ):
        raise HTTPException(status_code=404)
    return client


def _page(ref: str, plan: str, amount_minor: int, currency: str) -> str:
    amount = f"{amount_minor / 100:.2f} {html.escape(currency)}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Fake Paddle checkout (test only)</title>
</head>
<body data-ref="{html.escape(ref)}">
<main>
<h1>Fake Paddle checkout</h1>
<p>Test only. No real payment is made and nothing leaves this server.</p>
<p>Plan: {html.escape(plan)}. Amount: {amount} a month.</p>
<p><button type="button" data-outcome="pay">Pay</button>
<button type="button" data-outcome="fail">Decline the card</button></p>
<p id="note" role="status"></p>
</main>
<script src="{SCRIPT_PATH}"></script>
</body>
</html>
"""


@router.get(f"{CHECKOUT_PATH}/{{ref}}")
async def checkout_page(ref: str, request: Request) -> Response:
    client = _fake(request)
    found = client.checkout_of(ref) if _REF.fullmatch(ref) else None
    if found is None:
        raise HTTPException(status_code=404)
    price = found.price
    body = _page(ref, price.plan_id, price.amount_minor, price.currency)
    return HTMLResponse(body, headers=_NO_STORE)


@router.get(SCRIPT_PATH)
async def checkout_script(request: Request) -> Response:
    _fake(request)
    return Response(_SCRIPT, media_type="text/javascript", headers=_NO_STORE)


def _signed_post(request: Request) -> Post:
    """The webhook's own handler, called in process: what Paddle's server would POST."""
    app = request.app

    async def post(body: bytes, signature: str) -> int:
        sent = False

        async def receive() -> dict[str, object]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        scope = {
            "type": "http", "method": "POST", "path": webhook.PATH, "query_string": b"",
            "headers": [(HEADER.lower().encode(), signature.encode())],
            "client": ("127.0.0.1", 0), "scheme": "http", "server": ("fake-paddle", 80),
            "app": app, "state": {},
        }  # fmt: skip
        answer = await webhook.receive(StarletteRequest(scope, receive))
        return answer.status_code

    return post


@router.post(ACTION_PATH)
async def complete(
    ref: str,
    outcome: Literal["pay", "fail"],
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> JSONResponse:
    """The buyer pays or declines on the fake page: Paddle's notifications reach the webhook."""
    client = _fake(request)
    found = client.checkout_of(ref) if _REF.fullmatch(ref) else None
    if found is None or found.owner_id != ctx.user_id:
        raise HTTPException(status_code=404)  # unknown, already paid, or another buyer's
    before = len(client.outbox)
    if outcome == "pay":
        client.pay(ref)
    else:
        client.fail(ref)
    # Only this checkout's own notifications: another buyer's stuck one must not fail this answer.
    mine = {delivery.event_id for delivery in client.outbox[before:]}
    post = _signed_post(request)
    left = len(mine)
    for _ in range(_ROUNDS):
        order = [i for i, d in enumerate(client.outbox) if d.event_id in mine]
        left = len(order)
        if not order:
            break
        await client.deliver(post, order=order)
        left = sum(1 for d in client.outbox if d.event_id in mine)
    status = 200 if left == 0 else 502
    return JSONResponse({"outcome": outcome, "undelivered": left}, status_code=status,
                        headers=_NO_STORE)  # fmt: skip
