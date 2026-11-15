"""The content hash of a player class document (ADR 0034 section 4).

The player's rule (class format README, choice 6 and ADR 0003 section 6): ``sha256:`` and the hex
SHA-256 of the document's RFC 8785 canonical JSON. Pure: no I/O.
"""

import hashlib
from collections.abc import Mapping
from typing import Any

import rfc8785


def content_hash(document: Mapping[str, Any]) -> str:
    """``sha256:<64 hex>`` over the canonical (RFC 8785) form of ``document``."""
    return "sha256:" + hashlib.sha256(rfc8785.dumps(document)).hexdigest()
