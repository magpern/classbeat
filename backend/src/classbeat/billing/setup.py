"""Mount the Paddle side of billing on the app (ADR 0032 sections 2 to 4, #973).

With `billing_provider = dummy` (the default) nothing is mounted: there is no webhook route and
no client. With `fake_paddle` (refused in prod by the settings) the app gets the in-memory
`FakePaddleClient`, signing with the same secret the webhook verifies, the webhook's gate and the
route. `paddle` is refused by the settings until the real client exists, so it never gets here;
should it, nothing is mounted (no client means no route).
"""

from __future__ import annotations

from fastapi import FastAPI

from classbeat.billing.fake_paddle import FakePaddleClient
from classbeat.billing.webhook import router
from classbeat.billing.webhook_gate import WebhookGate
from classbeat.config import Settings
from classbeat.config_billing import webhook_secrets


def install(app: FastAPI, settings: Settings) -> None:
    keys = webhook_secrets(settings)
    if settings.billing_provider != "fake_paddle" or settings.environment == "prod" or not keys:
        return
    app.state.paddle_client = FakePaddleClient(secret=keys[0], base_url=settings.public_base_url)
    app.state.paddle_webhook = WebhookGate()
    app.include_router(router)
