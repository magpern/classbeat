"""The registration ceremony's options and verification through py_webauthn (ADR 0019 sections 2
and 4, #772). CPU only: no database, network or request.

Every call passes the RP ID and the one exact origin from the settings (``RelyingParty``), user
verification required, attestation ``none`` and the same three algorithms in the options and in the
verification (ES256, EdDSA, RS256), never the library's longer default. Whatever attestation
statement arrives drives no decision. Any failure of the library (its messages quote expected and
received values: origins, RP ids, challenges) becomes one ``PasskeyRefused`` with no text and no
chained cause, so nothing of the client's data reaches a log or a response.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from webauthn import generate_registration_options, verify_registration_response
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.options_to_json_dict import options_to_json_dict
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from classbeat.auth.passkey_policy import CHALLENGE_SECONDS, RP_NAME, kept_transports
from classbeat.config_passkeys import RelyingParty

ALGORITHMS = [
    COSEAlgorithmIdentifier.ECDSA_SHA_256,  # ES256 (-7)
    COSEAlgorithmIdentifier.EDDSA,  # EdDSA (-8)
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,  # RS256 (-257)
]
CREDENTIAL_ID_MAX = 1023  # WebAuthn's bound, and the column's check
PUBLIC_KEY_MAX = 2048  # the column's check


class PasskeyRefused(Exception):
    """The response was not accepted. Carries no detail on purpose."""

    def __init__(self) -> None:
        super().__init__("passkey refused")


@dataclass(frozen=True, slots=True)
class NewPasskey:
    """What registration stores: the credential, its COSE public key, its first counter, the
    backup flags and the transports section 7 keeps (display only)."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    backup_eligible: bool
    backup_state: bool
    transports: list[str]


def registration_options(
    rp: RelyingParty,
    *,
    user_handle: bytes,
    email: str,
    challenge: bytes,
    exclude: Sequence[bytes],
) -> dict[str, Any]:
    """The ``publicKey`` options of ``navigator.credentials.create`` as JSON (section 4)."""
    options = generate_registration_options(
        rp_id=rp.rp_id,
        rp_name=RP_NAME,
        user_name=email,
        user_id=user_handle,
        user_display_name=email,
        challenge=challenge,
        timeout=CHALLENGE_SECONDS * 1000,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in exclude],
        supported_pub_key_algs=ALGORITHMS,
    )
    return options_to_json_dict(options)


def verify_registration(
    rp: RelyingParty, credential: Mapping[str, Any], challenge: bytes
) -> NewPasskey:
    """Verify a ``navigator.credentials.create`` response against the stored challenge; raise
    ``PasskeyRefused`` for anything the library or the column bounds refuse."""
    try:
        verified = verify_registration_response(
            credential=dict(credential),
            expected_challenge=challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            require_user_presence=True,
            require_user_verification=True,
            supported_pub_key_algs=ALGORITHMS,
        )
    except Exception:  # untrusted input: every library or parser error is the same refusal
        raise PasskeyRefused from None
    if not 1 <= len(verified.credential_id) <= CREDENTIAL_ID_MAX:
        raise PasskeyRefused
    if not 1 <= len(verified.credential_public_key) <= PUBLIC_KEY_MAX:
        raise PasskeyRefused
    if not verified.user_verified:  # the library checked the UV flag; kept as a second guard
        raise PasskeyRefused
    response = credential.get("response")
    reported = response.get("transports") if isinstance(response, Mapping) else None
    return NewPasskey(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        backup_eligible=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
        backup_state=verified.credential_backed_up,
        transports=kept_transports(reported if isinstance(reported, list) else ()),
    )
