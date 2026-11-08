"""The pure passkey rules (ADR 0019 sections 4 and 7, #772): labels, transports and the
verification's refusal, which carries no library text. No database, no network."""

from datetime import date

import pytest

from classbeat.auth.passkey_policy import (
    LABEL_MAX,
    clean_label,
    default_label,
    kept_transports,
)
from classbeat.auth.passkey_webauthn import ALGORITHMS, PasskeyRefused, verify_registration
from classbeat.config_passkeys import RelyingParty


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("Phone", "Phone"),
        ("  padded  ", "padded"),
        ("x" * LABEL_MAX, "x" * LABEL_MAX),
        ("Åsa's YubiKey 5C", "Åsa's YubiKey 5C"),
        ("", None),
        ("   ", None),
        ("x" * (LABEL_MAX + 1), None),
        ("two\nlines", None),
        ("bell\x07", None),
        ("rtl‮override", None),
    ],
)
def test_clean_label(raw: str, stored: str | None) -> None:
    assert clean_label(raw) == stored


def test_the_default_label_names_the_date() -> None:
    assert default_label(date(2026, 11, 7)) == "Passkey 2026-11-07"


def test_only_the_known_transports_are_kept_once_in_a_fixed_order() -> None:
    reported = ["hybrid", "cable", "internal", "hybrid", 7, None, "smart-card", "USB"]
    assert kept_transports(reported) == ["internal", "hybrid", "smart-card"]


def test_the_three_algorithms_and_nothing_else() -> None:
    assert [int(alg) for alg in ALGORITHMS] == [-7, -8, -257]


def test_a_refusal_carries_no_library_text_and_no_cause() -> None:
    rp = RelyingParty(rp_id="classbeat.test", origin="https://classbeat.test")
    with pytest.raises(PasskeyRefused) as caught:
        verify_registration(rp, {"id": "secret-looking-value", "response": {}}, b"\x00" * 32)
    assert str(caught.value) == "passkey refused"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
