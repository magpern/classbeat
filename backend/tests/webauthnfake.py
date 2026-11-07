"""A software authenticator for the passkey tests (ADR 0019 section 11). No network, no device.

Keys are made per run (P-256 for ES256, Ed25519 for EdDSA). ``create`` answers the ``publicKey``
options of a register start the way a browser's ``PublicKeyCredential.toJSON()`` does: a real
``clientDataJSON``, a ``none`` attestation object with real authenticator data (RP ID hash, flags,
counter, AAGUID, credential id and COSE key), so py_webauthn's parsing and checks run unmodified.
Every field can be bent for a refusal test: origin, RP ID, challenge, flags, credential id.
"""

import base64
import hashlib
import json
import secrets
import struct
from dataclasses import dataclass, field
from typing import Any

import cbor2
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from authtools import ORIGIN

RP_ID = "classbeat.test"  # the host of authtools.ORIGIN

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class SoftAuthenticator:
    """One credential on one authenticator. ``alg`` is ``es256`` or ``eddsa``."""

    alg: str = "es256"
    sign_count: int = 0
    backup_eligible: bool = True
    backup_state: bool = True
    transports: list[str] = field(default_factory=lambda: ["internal", "hybrid"])
    credential_id: bytes = field(default_factory=lambda: secrets.token_bytes(16))
    _key: Any = None

    def __post_init__(self) -> None:
        if self.alg == "es256":
            self._key = ec.generate_private_key(ec.SECP256R1())
        elif self.alg == "eddsa":
            self._key = ed25519.Ed25519PrivateKey.generate()
        else:
            raise ValueError(self.alg)

    def cose_key(self) -> bytes:
        if self.alg == "es256":
            numbers = self._key.public_key().public_numbers()
            return cbor2.dumps(
                {
                    1: 2,
                    3: -7,
                    -1: 1,
                    -2: numbers.x.to_bytes(32, "big"),
                    -3: numbers.y.to_bytes(32, "big"),
                }
            )
        raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return cbor2.dumps({1: 1, 3: -8, -1: 6, -2: raw})

    def flags(self, *, up: bool = True, uv: bool = True) -> int:
        value = AT
        value |= UP if up else 0
        value |= UV if uv else 0
        value |= BE if self.backup_eligible else 0
        value |= BS if self.backup_state else 0
        return value

    def create(
        self,
        options: dict[str, Any],
        *,
        origin: str = ORIGIN,
        rp_id: str | None = None,
        challenge: str | None = None,
        up: bool = True,
        uv: bool = True,
        client_type: str = "webauthn.create",
    ) -> dict[str, Any]:
        """The JSON a browser sends to the register finish for these options."""
        public_key = options.get("publicKey", options)
        client_data = json.dumps(
            {
                "type": client_type,
                "challenge": challenge or public_key["challenge"],
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()
        rp_hash = hashlib.sha256((rp_id or public_key["rp"]["id"]).encode()).digest()
        attested = (
            bytes(16)  # AAGUID: none, as with attestation "none"
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self.cose_key()
        )
        auth_data = (
            rp_hash + bytes([self.flags(up=up, uv=uv)]) + struct.pack(">I", self.sign_count)
        ) + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": list(self.transports),
            },
        }
