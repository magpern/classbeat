"""One gateway answer's body, read raw and capped (ADR 0035 section 1).

A ``Content-Encoding`` other than identity is refused before a single byte is decoded (a small
gzip of a huge document must not expand: the body is read with ``aiter_raw``, never decoded). A
declared length over the cap is refused the same way, and the streamed count is checked too:
the rest of an oversized body is not read.
"""

import httpx


def _declared_too_big(response: httpx.Response, cap: int) -> bool:
    declared = response.headers.get("content-length")
    if declared is None:
        return False
    text = declared.strip()
    return not text.isdigit() or int(text) > cap


async def read_capped(response: httpx.Response, cap: int) -> bytes | None:
    """The raw body, or ``None`` when it must not be parsed (encoded, or over ``cap`` bytes)."""
    encoding = response.headers.get("content-encoding")
    if encoding is not None and encoding.strip().lower() not in ("", "identity"):
        return None
    if _declared_too_big(response, cap):
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_raw():
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)
