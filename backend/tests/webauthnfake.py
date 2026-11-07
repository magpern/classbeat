"""A software authenticator for the passkey tests (ADR 0019 section 11). No network, no device.

Keys are made per run (P-256 for ES256, Ed25519 for EdDSA). ``create`` answers the ``publicKey``
options of a register start the way a browser's ``PublicKeyCredential.toJSON()`` does: a real
``clientDataJSON``, a ``none`` attestation object with real authenticator data (RP ID hash, flags,
counter, AAGUID, credential id and COSE key), so py_webauthn's parsing and checks run unmodified.
``get`` answers the options of a sign-in start the same way (``authenticatorData``, a signature
over it and the client data hash, the user handle). Every field can be bent for a refusal test:
origin, RP ID, challenge, flags, credential id, user handle, signature.
"""

import base64
import hashlib
import json
import secrets
import struct
from dataclasses import dataclass, field
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
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
    """One credential on one authenticator. ``alg`` is ``es256``, ``eddsa`` or ``es512`` (an
    algorithm py_webauthn knows but classbeat does not offer, for the refusal tests)."""

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
        elif self.alg == "es512":
            self._key = ec.generate_private_key(ec.SECP521R1())
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
        if self.alg == "es512":
            numbers = self._key.public_key().public_numbers()
            return cbor2.dumps(
                {
                    1: 2,
                    3: -36,
                    -1: 3,
                    -2: numbers.x.to_bytes(66, "big"),
                    -3: numbers.y.to_bytes(66, "big"),
                }
            )
        raw = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return cbor2.dumps({1: 1, 3: -8, -1: 6, -2: raw})

    def sign(self, data: bytes) -> bytes:
        if self.alg == "es256":
            return self._key.sign(data, ec.ECDSA(hashes.SHA256()))
        if self.alg == "es512":
            return self._key.sign(data, ec.ECDSA(hashes.SHA512()))
        return self._key.sign(data)

    def flags(self, *, up: bool = True, uv: bool = True, attested: bool = True) -> int:
        value = AT if attested else 0
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

    def get(
        self,
        options: dict[str, Any],
        user_handle: bytes | None,
        *,
        origin: str = ORIGIN,
        rp_id: str | None = None,
        challenge: str | None = None,
        up: bool = True,
        uv: bool = True,
        client_type: str = "webauthn.get",
        bad_signature: bool = False,
    ) -> dict[str, Any]:
        """The JSON a browser sends to the sign-in finish for these options. ``sign_count`` is
        used as it is (a test sets it); ``user_handle`` None leaves ``userHandle`` out."""
        public_key = options.get("publicKey", options)
        client_data = json.dumps(
            {
                "type": client_type,
                "challenge": challenge or public_key["challenge"],
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()
        rp_hash = hashlib.sha256((rp_id or public_key["rpId"]).encode()).digest()
        auth_data = (
            rp_hash
            + bytes([self.flags(up=up, uv=uv, attested=False)])
            + struct.pack(">I", self.sign_count)
        )
        signature = self.sign(auth_data + hashlib.sha256(client_data).digest())
        if bad_signature:
            signature = self.sign(b"something else")
        response: dict[str, Any] = {
            "clientDataJSON": b64url(client_data),
            "authenticatorData": b64url(auth_data),
            "signature": b64url(signature),
        }
        if user_handle is not None:
            response["userHandle"] = b64url(user_handle)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": response,
        }
