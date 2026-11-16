"""#1126: the derived keys are pinned byte for byte, so stored digests and signed values stay valid.

The expected values were produced by the code before the SwedeBeat account key moved onto
``classbeat.auth.derive_key``, from a fixed non-secret test input.
"""

import uuid

import pytest

from classbeat.auth import derive_key
from classbeat.connections.swedebeat_account import account_ref_for

SECRET = bytes(range(32))  # a test input, not a real secret

PINNED = {
    "csrf": "9568094e22970732c6f9864c8d931d19d8455c268ce6cb76932e6ea1868ec3a1",
    "rate-limit": "84beaab243d33b6145daaf8f33ac421da7467afefdde0b00ffeb4f15ecfc2a67",
    "device": "126f03d65d6696d23f784a9b31cfcde264e736e97b1e8993b213b7f70dbc1ec6",
    "recovery-codes": "44f99cc56033f60ec7cda261480dad9545355f544db5528f3dea54c5c041cecc",
    "totp-v1": "d2ff2ce66d1a5345f29204f2bb80674fcdc4595f9d677db9f33fb126a633af36",
    "totp-pending": "113d0a0e543283db12b662ce0d28c3d1f8ef21180dac28f211b54bba84072f3c",
    "session-client": "0a04192665855fae7531b1c5cc865d0754d8fc515eb6dcb9efc54ca9172588a4",
    "security-log": "18d285017d240433cac40df0588e886dfac81e2ba58a24eba2cda3171236c73a",
    "mail-unsubscribe": "0487ba3f8e32d0487cbf760a95f24bb01c0e38e208f351e580408587cddb5c37",
    "swedebeat-account": "b1691e6194f765dc0d90751014aa9e9ae7ff61d01524b6e35b2336a1c5c980cc",
}


@pytest.mark.parametrize(("info", "hex_key"), PINNED.items())
def test_each_label_derives_exactly_as_before(info: str, hex_key: str) -> None:
    assert derive_key(SECRET, info).hex() == hex_key


def test_the_swedebeat_account_digest_is_unchanged() -> None:
    user = uuid.UUID(int=1)
    assert account_ref_for(SECRET, user, "12345") == "2fb3313f04f938f1a96ed060a22e0ca3"
