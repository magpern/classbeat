"""The gateway client of one call (ADR 0035 section 1): the web's login and the worker's match and
session check each open one here, ``async with``, so neither the cookie jar nor the connection pool
outlives the call. ``open_upload_client`` is the merge-publish's (ADR 0036 section 8: its own
900 s deadline; SR C7).

``open_client`` is ``classbeat.egress_gateway.swedebeat_gateway_async_client`` (the one exact
origin installed at start, no redirect, no default header, a total deadline) and nothing else. It
is the seam the tests replace with the in-process fake (``tests/gatewayfake.py``); callers go
through the module attribute (``opener.open_client``) so the replacement reaches them.
"""

import httpx

from classbeat import egress_gateway


def open_client(total_seconds: float) -> httpx.AsyncClient:
    """The production gateway client with ``total_seconds`` as its deadline (clamped)."""
    return egress_gateway.swedebeat_gateway_async_client(total_seconds)


def open_upload_client() -> httpx.AsyncClient:
    """The production gateway client of one merge-publish (``MERGE_SECONDS``)."""
    return egress_gateway.swedebeat_gateway_upload_client()
