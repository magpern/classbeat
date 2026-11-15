"""Content hash of a class document: sha256 over RFC 8785 canonical JSON. No I/O."""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785


def content_hash(document: Any) -> str:
    digest = hashlib.sha256(rfc8785.dumps(document)).hexdigest()
    return f"sha256:{digest}"
