"""Pure host, origin and network parsing helpers for ``classbeat.config`` (no I/O).

Re-exported from ``classbeat.config``, so callers keep importing them from there.
"""

import ipaddress
from urllib.parse import urlsplit

# Hosts where unencrypted SMTP (``smtp_tls = none``) is allowed: loopback and the compose service.
PLAIN_SMTP_HOSTS = frozenset({"localhost", "mailpit"})


def _is_local_host(host: str) -> bool:
    if host.lower() in PLAIN_SMTP_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def public_origin(url: str) -> str:
    """``scheme://host[:port]`` of ``url``, lower case, as a browser sends it in ``Origin``."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    port = f":{parts.port}" if parts.port is not None and parts.port != default else ""
    return f"{parts.scheme.lower()}://{host.lower()}{port}"


def parse_networks(
    value: str,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse a comma-separated list of addresses and CIDR ranges."""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in value.split(","):
        if item.strip():
            try:
                networks.append(ipaddress.ip_network(item.strip(), strict=False))
            except ValueError:
                raise ValueError("trusted_proxies must list IP addresses or CIDR ranges") from None
    return tuple(networks)


def check_public_base_url(value: str) -> str:
    """``public_base_url`` as its origin, refused unless it is an http(s) origin without a path."""
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ValueError("public_base_url must be an http(s) origin without a path")
    return public_origin(value)


def check_prod_trusted_proxies(
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> None:
    """ADR 0003 section 9, for prod: a trusted proxy chooses the client address that rate limits
    bucket by. The one trusted peer is the reverse proxy on a private Docker network (swag,
    docs/runbooks/vps-deploy.md); a public range (Cloudflare's included: swag restores the client
    from CF-Connecting-IP, never this app) would let the open internet pick its own."""
    if not all(net.is_private for net in networks):
        raise ValueError("trusted_proxies must be private addresses when environment is prod")
    # #495: exactly one host (/32 or /128), never a range or a list. A range on the proxy
    # network would let any other container there forge X-Forwarded-For and choose its
    # rate-limit bucket. Empty (nothing trusted) stays allowed: it fails closed.
    if len(networks) > 1 or any(net.prefixlen != net.max_prefixlen for net in networks):
        raise ValueError(
            "trusted_proxies must be one address (not a range or a list) when environment is prod"
        )
