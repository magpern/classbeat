"""The client address behind the owner's swag/nginx proxy, with and without Cloudflare (#492).

The production setting trusts exactly one peer: swag's fixed address on the proxy network
(``compose.prod.yaml``, ``PROXY_IP``), or the web-out gateway with the loopback overlay. swag
restores the visitor from ``CF-Connecting-IP`` only for Cloudflare's ranges (nginx real-ip) and
sends ``X-Forwarded-For: $remote_addr`` (docs/runbooks/swag-classbeat.subdomain.conf). The
requests below are what that proxy forwards, played as ASGI scopes against both readers of the
address: ``auth.request_ip`` (rate limits, sessions, auth events) and the body cap's log line.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from classbeat import bodylimit
from classbeat.auth import request_ip
from classbeat.config import Settings

SWAG = "172.29.90.10"  # swag on the proxy network (the runbook's example)
OTHER_ON_PROXY_NETWORK = "172.29.90.130"  # any other container there (from --ip-range .128/25)
WEB_OUT_GATEWAY = "172.29.80.1"  # a host process reaching web over web-out
CLOUDFLARE_EDGE = "173.245.48.5"  # inside a Cloudflare range
VISITOR = "198.51.100.7"
VISITOR_V6 = "2001:db8:1234:5678::9"
FORGED = "6.6.6.6"


def _settings(trusted: str) -> Settings:
    return Settings(
        environment="prod", public_base_url="https://classbeat.example", trusted_proxies=trusted
    )


def _scope(peer: str, headers: list[tuple[str, str]], trusted: str) -> dict[str, object]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": (peer, 40000),
        "app": SimpleNamespace(state=SimpleNamespace(settings=_settings(trusted))),
    }


def _both(peer: str, headers: list[tuple[str, str]], trusted: str = SWAG) -> str:
    scope = _scope(peer, headers, trusted)
    from_auth = request_ip(Request(scope))  # pyright: ignore[reportArgumentType]
    from_body_cap = bodylimit._client(scope)  # pyright: ignore[reportArgumentType]
    assert from_auth == from_body_cap
    return from_auth


def test_swag_overwriting_the_header_gives_the_visitor() -> None:
    # The sample config: proxy_set_header X-Forwarded-For $remote_addr.
    assert _both(SWAG, [("X-Forwarded-For", VISITOR)]) == VISITOR
    assert _both(SWAG, [("X-Forwarded-For", VISITOR_V6)]) == VISITOR_V6


def test_swag_appending_keeps_forged_entries_out() -> None:
    # swag's stock proxy.conf appends ($proxy_add_x_forwarded_for): whatever the visitor sent is
    # left of the address swag saw, and only the right-most entry not from swag counts.
    header = f"{FORGED}, 10.0.0.1, {VISITOR}"
    assert _both(SWAG, [("X-Forwarded-For", header)]) == VISITOR
    assert _both(SWAG, [("X-Forwarded-For", FORGED), ("X-Forwarded-For", VISITOR)]) == VISITOR


def test_behind_cloudflare_the_restored_visitor_is_used() -> None:
    # Proxied mode with real-ip: swag's $remote_addr is CF-Connecting-IP (Cloudflare overwrites
    # it), Cloudflare's own X-Forwarded-For list is replaced.
    assert _both(SWAG, [("X-Forwarded-For", VISITOR), ("CF-Connecting-IP", VISITOR)]) == VISITOR


def test_cloudflare_ranges_are_never_trusted_by_the_app() -> None:
    # Proxied mode without real-ip configured at swag, appending: the Cloudflare edge is the
    # right-most entry and becomes the client (visitors share buckets, but none can choose one).
    header = f"{FORGED}, {VISITOR}, {CLOUDFLARE_EDGE}"
    assert _both(SWAG, [("X-Forwarded-For", header)]) == CLOUDFLARE_EDGE


def test_cf_connecting_ip_is_never_read_by_the_app() -> None:
    assert _both(SWAG, [("CF-Connecting-IP", FORGED)]) == SWAG
    headers = [("CF-Connecting-IP", FORGED), ("X-Forwarded-For", FORGED)]
    assert _both(OTHER_ON_PROXY_NETWORK, headers) == OTHER_ON_PROXY_NETWORK


@pytest.mark.parametrize("peer", [OTHER_ON_PROXY_NETWORK, WEB_OUT_GATEWAY, VISITOR, "10.0.0.1"])
def test_only_swag_may_name_the_client(peer: str) -> None:
    assert _both(peer, [("X-Forwarded-For", FORGED)]) == peer


def test_a_forged_swag_address_in_the_header_does_not_skip_the_visitor() -> None:
    # An entry equal to swag is skipped as a proxy hop, so the walk ends at the one swag added.
    assert _both(SWAG, [("X-Forwarded-For", f"{FORGED}, {VISITOR}, {SWAG}")]) == VISITOR


def test_loopback_overlay_trusts_the_web_out_gateway_only() -> None:
    # compose.prod.loopback.yaml: the proxy reaches 127.0.0.1:<port>, Docker's port relay shows
    # the web-out gateway as the peer; swag's own address is then not trusted.
    trusted = WEB_OUT_GATEWAY
    assert _both(WEB_OUT_GATEWAY, [("X-Forwarded-For", VISITOR)], trusted) == VISITOR
    assert _both(SWAG, [("X-Forwarded-For", FORGED)], trusted) == SWAG
