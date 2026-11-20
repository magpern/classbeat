"""The spotdown client of one call (ADR 0040 section 10): each call of ``client`` opens one here,
``async with``, so no connection pool outlives the call.

``open_client`` is ``classbeat.egress_spotdown.spotdown_async_client`` and ``open_upload_client``
its upload twin, and nothing else. They are the seam the tests replace with the in-process fake
(``tests/spotdownfake.py``); callers go through the module attribute (``opener.open_client``) so
the replacement reaches them.
"""

import httpx

from classbeat import egress_spotdown


def open_client(total_seconds: float) -> httpx.AsyncClient:
    """The production spotdown client with ``total_seconds`` as its deadline (clamped)."""
    return egress_spotdown.spotdown_async_client(total_seconds)


def open_upload_client() -> httpx.AsyncClient:
    """The production spotdown client of one streamed upload."""
    return egress_spotdown.spotdown_upload_client()
