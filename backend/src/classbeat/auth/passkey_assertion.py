"""The sign-in ceremony's options and the assertion check through py_webauthn (ADR 0019 sections
2, 5 and 10, #773). CPU only: no database, network or request.

The options are the same for everyone: no ``allowCredentials`` (discoverable credentials), so a
start reveals no account; user verification required.

``verify_assertion`` first parses the response and checks, in this order, what section 10 names a
reason category for (the client data type, the challenge, the origin, the RP ID hash, the UP and
UV flags) and that the stored key's COSE algorithm is one of the three the options offer, because
py_webauthn 3.0.1 takes no ``supported_pub_key_algs`` on authentication and verifies with whatever
algorithm the key names. Only then does the library verify everything again and check the
signature, with ``credential_current_sign_count=0`` so that its own counter check can never fire:
the counter rule is the finish's conditional ``UPDATE`` alone (section 7). Every refusal is one
``AssertionRefused`` carrying a fixed category, never the library's text (which quotes origins, RP
ids and challenges) and never a chained cause.
"""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from webauthn import generate_authentication_options, verify_authentication_response
from webauthn.helpers import (
    base64url_to_bytes,
    decode_credential_public_key,
    parse_authentication_credential_json,
    parse_authenticator_data,
    parse_client_data_json,
)
from webauthn.helpers.options_to_json_dict import options_to_json_dict
from webauthn.helpers.structs import (
    ClientDataType,
    CredentialDeviceType,
    UserVerificationRequirement,
)

from classbeat.auth.passkey_policy import CHALLENGE_SECONDS
from classbeat.auth.passkey_webauthn import ALGORITHMS, CREDENTIAL_ID_MAX, PasskeyRefused
from classbeat.config_passkeys import RelyingParty

Reason = Literal[
    "challenge", "origin", "rp_id", "user_handle", "uv", "signature", "backup_flag", "format"
]


class AssertionRefused(PasskeyRefused):
    """The assertion was not accepted; ``reason`` is a fixed category (section 10), no detail."""

    def __init__(self, reason: Reason) -> None:
        super().__init__()
        self.reason: Reason = reason


@dataclass(frozen=True, slots=True)
class Asserted:
    """What a verified assertion tells the finish: the authenticator's counter and backup state."""

    sign_count: int
    backup_eligible: bool
    backup_state: bool


def from_b64url(value: str) -> bytes | None:
    """Base64url text as bytes; None when it is not base64url."""
    try:
        return base64url_to_bytes(value)
    except (ValueError, TypeError):
        return None


def authentication_options(rp: RelyingParty, *, challenge: bytes) -> dict[str, Any]:
    """The ``publicKey`` options of ``navigator.credentials.get`` as JSON (section 5)."""
    options = generate_authentication_options(
        rp_id=rp.rp_id,
        challenge=challenge,
        timeout=CHALLENGE_SECONDS * 1000,
        allow_credentials=[],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json_dict(options)


def credential_id_of(credential: Mapping[str, Any]) -> bytes:
    """The credential id the response names (``rawId``, which must equal ``id``); ``format`` when
    it is missing, malformed or longer than WebAuthn's bound."""
    raw, named = credential.get("rawId"), credential.get("id")
    if not isinstance(raw, str) or raw != named:
        raise AssertionRefused("format")
    found = from_b64url(raw)
    if found is None or not 1 <= len(found) <= CREDENTIAL_ID_MAX:
        raise AssertionRefused("format")
    return found


def user_handle_matches(credential: Mapping[str, Any], expected: bytes | None) -> bool:
    """``userHandle`` is present and equals the credential's user's handle (section 5, step 3)."""
    response = credential.get("response")
    value = response.get("userHandle") if isinstance(response, Mapping) else None
    handle = from_b64url(value) if isinstance(value, str) and value else None
    if handle is None or expected is None:
        return False
    return hmac.compare_digest(handle, expected)


def verify_assertion(
    rp: RelyingParty,
    credential: Mapping[str, Any],
    challenge: bytes,
    public_key: bytes,
) -> Asserted:
    """Check a ``navigator.credentials.get`` response against the taken challenge and the stored
    public key; raise ``AssertionRefused`` with the first category that fails."""
    _precheck(rp, credential, challenge, public_key)
    try:
        verified = verify_authentication_response(
            credential=dict(credential),
            expected_challenge=challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            credential_public_key=public_key,
            credential_current_sign_count=0,  # section 2: the counter rule is ours alone
            require_user_verification=True,
        )
    except Exception:  # untrusted input: whatever the library says, it is the same refusal
        raise AssertionRefused("signature") from None
    if not verified.user_verified:  # checked above and by the library; kept as a third guard
        raise AssertionRefused("uv")
    return Asserted(
        sign_count=verified.new_sign_count,
        backup_eligible=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
        backup_state=verified.credential_backed_up,
    )


def _precheck(
    rp: RelyingParty, credential: Mapping[str, Any], challenge: bytes, public_key: bytes
) -> None:
    try:
        parsed = parse_authentication_credential_json(dict(credential))
        client = parse_client_data_json(parsed.response.client_data_json)
        data = parse_authenticator_data(parsed.response.authenticator_data)
    except Exception:
        raise AssertionRefused("format") from None
    if client.type != ClientDataType.WEBAUTHN_GET:
        raise AssertionRefused("format")
    if not hmac.compare_digest(client.challenge, challenge):
        raise AssertionRefused("challenge")
    if client.origin != rp.origin:
        raise AssertionRefused("origin")
    if not hmac.compare_digest(data.rp_id_hash, hashlib.sha256(rp.rp_id.encode()).digest()):
        raise AssertionRefused("rp_id")
    if not data.flags.up or not data.flags.uv:
        raise AssertionRefused("uv")
    try:
        algorithm = decode_credential_public_key(public_key).alg
    except Exception:
        raise AssertionRefused("format") from None
    if algorithm not in ALGORITHMS:
        raise AssertionRefused("format")
