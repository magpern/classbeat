"""A Spotify answer's body, decoded and capped (#1149). Pure: no I/O.

classbeat asks Spotify for ``Accept-Encoding: identity``, but a server may compress anyway.
The body is read raw (``aiter_raw``) and decoded here, so the cap counts **decoded** bytes and
decompression stops at the cap: ``zlib``'s ``max_length`` bounds every step, so a small
compressed answer that expands without end (a decompression bomb) never grows past
``cap + 1`` bytes in memory. httpx's own decoding (``aiter_bytes``) would expand each raw chunk
whole before any cap could be checked.

Identity, ``gzip`` (``x-gzip``) and ``deflate`` (zlib format, as RFC 9110 defines it) are
decoded; any other or a stacked encoding, a broken or a truncated compressed body is refused.
So is a compressed body with anything after the end of its stream (padding, or bytes after a
gzip member), and one whose raw size passes ``cap + RAW_SLACK`` (#1152): the read stops there
instead of going on to the end of the answer.
"""

import zlib
from typing import Final

_GZIP_WBITS: Final = 16 + zlib.MAX_WBITS
RAW_SLACK: Final = 1024  # raw bytes a compressed body may carry beyond the decoded cap
_WBITS: Final = {"gzip": _GZIP_WBITS, "x-gzip": _GZIP_WBITS, "deflate": zlib.MAX_WBITS}


class CappedBody:
    """Feed the raw chunks; ``feed`` says when to stop reading. ``body`` is the decoded body,
    at most ``cap + 1`` bytes (``cap + 1`` means "too long"), or ``None`` when refused."""

    def __init__(self, content_encoding: str | None, cap: int) -> None:
        if cap < 0:
            raise ValueError("a cap is not negative")
        coding = (content_encoding or "").strip().lower() or "identity"
        self._cap = cap
        self._parts: list[bytes] = []
        self._size = 0
        self._raw = 0
        self._refused = coding != "identity" and coding not in _WBITS
        self._inflate = zlib.decompressobj(_WBITS[coding]) if coding in _WBITS else None

    def _keep(self, data: bytes) -> None:
        self._parts.append(data)
        self._size += len(data)

    def feed(self, chunk: bytes) -> bool:
        """Take one raw chunk. False: stop reading (past the cap, or refused)."""
        if self._refused or self._size > self._cap:
            return False
        if self._inflate is None:
            self._keep(chunk[: self._cap + 1 - self._size])
            return self._size <= self._cap
        self._raw += len(chunk)
        if self._raw > self._cap + RAW_SLACK or (self._inflate.eof and chunk):
            self._refused = True  # too much raw, or data after the end of the stream
            return False
        data = chunk
        try:
            while data and not self._inflate.eof:
                self._keep(self._inflate.decompress(data, self._cap + 1 - self._size))
                if self._size > self._cap:
                    return False
                data = self._inflate.unconsumed_tail
        except zlib.error:
            self._refused = True
            return False
        if self._inflate.eof and self._inflate.unused_data:
            self._refused = True  # trailing bytes in the same chunk as the end
            return False
        return True

    def body(self) -> bytes | None:
        """The decoded body, or ``None``: a refused encoding, a broken compressed body, or one
        that ended before its end (unless it was already past the cap) or had data after it."""
        if self._refused:
            return None
        truncated = self._inflate is not None and not self._inflate.eof
        if truncated and self._size <= self._cap:
            return None
        return b"".join(self._parts)
