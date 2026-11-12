"""The environment's feature switches, a field group of ``classbeat.config.Settings``.

Pure: no I/O. The secret files a feature needs (``swedebeat_app``, ``turnstile_secret_key``) and
the checks that combine fields stay on ``Settings`` in ``classbeat.config``. Never instantiated on
its own.

A ``BaseSettings`` (not a plain ``BaseModel``) only so its ``model_config`` type matches
``Settings``'; the mixin adds no config of its own and declares no ``SecretStr`` field.
"""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings

from classbeat.config_turnstile import check_site_key as check_turnstile_site_key


class FeatureSettings(BaseSettings):
    """Kill switches (off by default) and the public, non-secret settings of those features."""

    # ADR 0012 §2: off, the DeepSeek host is not on the worker's list and no DeepSeek provider is
    # built for a job. The owner turns it on only after ADR 0012 §6 holds.
    deepseek_enabled: bool = False
    # ADR 0015: the Swedebeat connection. ``swedebeat_enabled`` is the environment's kill switch
    # (off by default; the admin flag ``swedebeat_enabled`` can only tighten it, ADR 0011 section
    # 7). The app key is ``Settings.swedebeat_app`` (``classbeat.config``).
    swedebeat_enabled: bool = False
    # ADR 0018 section 5: running classes. The environment's kill switch (off by default; the
    # admin flag ``running_enabled`` can only tighten it), read by the web (admit) and the worker
    # (a queued running job checks it again).
    running_enabled: bool = False
    # ADR 0007 amendments of 2026-11-03 (soundless classes, #527) and 2026-11-08 (#824): the
    # environment's kill switch (off by default). There is no admin flag; every plan's model route
    # must also cover soundless (``classbeat.flags.effective``).
    soundless_enabled: bool = False
    # ADR 0024 (#647): connect MOWL by scanning a QR code with the MOWL phone app. The
    # environment's switch, on by default (owner decision 2026-11-09, ADR 0028; it was off until
    # the owner's live scan); the password form stays either way.
    mowl_qr_enabled: bool = True
    # ADR 0025 (#721): Cloudflare Turnstile on sign-in, sign-up, the provider-email step and the
    # reset request. Off by default (dev, CI, tests); on, it needs the public site key and the
    # ``turnstile_secret_key`` secret file (web only, never the environment), unless
    # ``turnstile_fake`` (the e2e stack's local stand-in, no network) is on, which prod refuses.
    turnstile_enabled: bool = False
    turnstile_fake: bool = False
    turnstile_site_key: str | None = None
    # ADR 0019 (#771): passkeys, off by default (web only). On, it needs the RP ID, the exact host
    # (production classbeat.eu, test and beta classbeat.pernemark.se); ``config_passkeys``.
    passkeys: bool = False
    webauthn_rp_id: str | None = None

    # ADR 0031 section 6 (#948): the environment's billing switch. Off, and `true` is refused
    # while no real provider exists (M7b): only the Dummy provider runs, for admins and testers.
    billing_enabled: bool = False
    # ADR 0032 section 4 (#973): which provider a checkout uses. ``fake_paddle`` (test and compose
    # only, refused in prod) runs the Paddle flow over ``billing.fake_paddle``; ``paddle`` is
    # refused until the real HTTP client exists (M7b-real, the owner's step).
    billing_provider: Literal["dummy", "fake_paddle", "paddle"] = "dummy"

    @field_validator("billing_provider")
    @classmethod
    def _refuse_real_paddle(cls, value: str) -> str:
        if value == "paddle":
            raise ValueError(
                "billing_provider cannot be paddle: the real Paddle client is not written yet"
            )
        return value

    @field_validator("billing_enabled")
    @classmethod
    def _refuse_billing_on(cls, value: bool) -> bool:
        if value:
            raise ValueError("billing_enabled cannot be true: there is no real billing provider")
        return value

    @field_validator("turnstile_site_key")
    @classmethod
    def _check_turnstile_site_key(cls, value: str | None) -> str | None:
        return check_turnstile_site_key(value)
