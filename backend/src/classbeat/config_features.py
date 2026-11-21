"""The environment's feature switches, a field group of ``classbeat.config.Settings``.

Pure: no I/O. The secret files a feature needs (``swedebeat_gateway_key``,
``turnstile_secret_key``) and the checks that combine fields stay on ``Settings`` in
``classbeat.config``. Never instantiated on its own.

A ``BaseSettings`` (not a plain ``BaseModel``) only so its ``model_config`` type matches
``Settings``'; the mixin adds no config of its own and declares no ``SecretStr`` field.
"""

import re
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings

from classbeat.config_turnstile import check_site_key as check_turnstile_site_key
from classbeat.player.base_url_policy import player_base_url as check_player_base_url
from classbeat.spotdown.owner_list_policy import canonical as check_owner_list
from classbeat.spotdown.sources_policy import canonical as check_spotdown_sources
from classbeat.spotdown.url_policy import spotdown_url as check_spotdown_url
from classbeat.swedebeat_gateway.url_policy import gateway_url as check_swedebeat_gateway_url
from classbeat.swedebeat_studio.config_policy import canonical as check_swedebeat_studio

# ADR 0034 section 1: the player's client id is 1 to 128 letters, digits, ``.``, ``_`` or ``-``.
# Its redirect address is an absolute ``https`` URL (``http`` only on a loopback host, for local
# stacks) with a host, no user info and no fragment, at most 512 characters; the authorize
# request must name it exactly.
_PLAYER_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_PLAYER_REDIRECT = re.compile(
    r"(?P<scheme>https?)://(?P<host>\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?"
    r"(?:[/?][^\s#]*)?"
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
PLAYER_REDIRECT_MAX = 512


def check_player_client_id(value: str | None) -> str | None:
    if value is not None and not _PLAYER_CLIENT_ID.fullmatch(value):
        raise ValueError("oidc_player_client_id must be 1 to 128 letters, digits, '.', '_' or '-'")
    return value


def check_player_redirect_uri(value: str | None) -> str | None:
    if value is None:
        return None
    parts = (
        _PLAYER_REDIRECT.fullmatch(value)
        if len(value) <= PLAYER_REDIRECT_MAX and value.isprintable()
        else None
    )
    host = parts["host"].lower() if parts else None
    if parts is None or not (parts["scheme"] == "https" or host in _LOOPBACK_HOSTS):
        raise ValueError(
            "oidc_player_redirect_uri must be an absolute https URL without a fragment"
        )
    return value


class FeatureSettings(BaseSettings):
    """Kill switches (off by default) and the public, non-secret settings of those features."""

    # ADR 0012 §2: off, the DeepSeek host is not on the worker's list and no DeepSeek provider is
    # built for a job. The owner turns it on only after ADR 0012 §6 holds.
    deepseek_enabled: bool = False
    # ADR 0015: the Swedebeat connection. ``swedebeat_enabled`` is the environment's kill switch
    # (off by default; the admin flag ``swedebeat_enabled`` can only tighten it, ADR 0011 section
    # 7). SwedeBeat is reached only through the gateway below (ADR 0035).
    swedebeat_enabled: bool = False
    # ADR 0035 section 1: the SwedeBeat gateway's origin (web's login, the worker's match). Unset,
    # SwedeBeat is unavailable; a value that breaks a rule of ``swedebeat_gateway.url_policy``
    # refuses startup of both. Its client key is ``Settings.swedebeat_gateway_key`` (a file).
    swedebeat_gateway_url: str | None = None
    # ADR 0036 sections 2 and 10: the SwedeBeat studio, a comma list of ``design``,
    # ``swedebeat_playlist``, ``swedebeat_mix`` and ``mowl``; empty (the default) is off.
    # ``swedebeat_studio.config_policy`` refuses anything else at startup.
    swedebeat_studio: str = ""
    # ADR 0018 section 5: running classes. The environment's kill switch (off by default; the
    # admin flag ``running_enabled`` can only tighten it), read by the web (admit) and the worker
    # (a queued running job checks it again).
    running_enabled: bool = False
    # ADR 0038 section 2 (#1185): the composer flow. The environment's switch (off by default);
    # only a stored admin flag ``composer_flow`` of ``on`` turns it on under it. Not set in
    # ``compose.prod.yaml`` until the owner's live check (``scripts/check-prod-compose.sh``).
    composer_flow: bool = False
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
    # ADR 0034 section 1 (#1056): the one OpenID Connect client, classbeat-player. With neither,
    # the provider is off (``/oidc`` answers 404); with both, its three secret files must exist
    # (``classbeat.auth.oidc.provider``). The redirect address is compared exactly.
    oidc_player_client_id: str | None = None
    oidc_player_redirect_uri: str | None = None
    # ADR 0034 section 2 (#1058): where the worker POSTs the signed events (the player's one
    # host, ``classbeat.egress``). Unset, nothing is delivered and events fail visibly after 7
    # days; a value that breaks a rule of ``player.base_url_policy`` refuses startup.
    player_base_url: str | None = None
    # ADR 0040 section 10 (#1301): spotdown's internal origin (``spotdown.url_policy``), worker
    # only. Unset (the default) is off: nothing calls spotdown. Its key is
    # ``Settings.spotdown_service_key`` (a file); the sources it may use are
    # ``spotdown_sources``, a comma list of ``upload`` and ``youtube`` (empty: none).
    spotdown_url: str | None = None
    spotdown_sources: str = ""
    # ADR 0040 section 9 (#1301 part 3): shadow mode for studio tracks, worker only. On (and with
    # spotdown installed and ``upload`` among the sources), each downloaded studio track is also
    # analysed by spotdown and only the comparison is recorded (``spotdown.shadow_config``); the
    # design keeps today's analysis. Off by default.
    spotdown_shadow: bool = False
    # ADR 0040 sections 2 and 8 (#1303, SD-P6 stage A): the Spotify song pick's ``/v2/match``
    # stage, worker only. ``off`` (the default) is today's pick; ``allowlist`` matches the pool
    # with spotdown for the owners in ``spotdown_spotify_owners`` (user ids, comma separated),
    # with ``youtube`` among the sources (``spotdown.song_match_config``). No value switches it
    # on for everyone: that default waits for the Spotify gates (owner decision #1298).
    spotdown_spotify: Literal["off", "allowlist"] = "off"
    spotdown_spotify_owners: str = ""

    @field_validator("swedebeat_gateway_url")
    @classmethod
    def _check_swedebeat_gateway_url(cls, value: str | None) -> str | None:
        return None if value is None else check_swedebeat_gateway_url(value)

    @field_validator("swedebeat_studio")
    @classmethod
    def _check_swedebeat_studio(cls, value: str) -> str:
        return check_swedebeat_studio(value)

    @field_validator("player_base_url")
    @classmethod
    def _check_player_base_url(cls, value: str | None) -> str | None:
        return None if value is None else check_player_base_url(value)

    @field_validator("spotdown_url")
    @classmethod
    def _check_spotdown_url(cls, value: str | None) -> str | None:
        return None if value is None else check_spotdown_url(value)

    @field_validator("spotdown_sources")
    @classmethod
    def _check_spotdown_sources(cls, value: str) -> str:
        return check_spotdown_sources(value)

    @field_validator("spotdown_spotify_owners")
    @classmethod
    def _check_spotdown_spotify_owners(cls, value: str) -> str:
        return check_owner_list(value, "spotdown_spotify_owners")

    @field_validator("oidc_player_client_id")
    @classmethod
    def _check_player_client_id(cls, value: str | None) -> str | None:
        return check_player_client_id(value)

    @field_validator("oidc_player_redirect_uri")
    @classmethod
    def _check_player_redirect_uri(cls, value: str | None) -> str | None:
        return check_player_redirect_uri(value)

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
