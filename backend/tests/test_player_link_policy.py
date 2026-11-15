"""The player link's pure rules (``player_link.link_policy``, ADR 0034 section 3): the link
address, the token shapes and the binding decision. No I/O."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from classbeat.player_link.link_policy import (
    callback_uri,
    challenge_of,
    decide,
    is_token,
    link_url,
    new_state,
    new_verifier,
)

ISSUER = "https://classbeat.test"
SUBJECT = "pairwise-subject-of-alice"
SOURCE = "cbp_src_" + "a" * 43


def answer(**overrides: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "source_token": SOURCE,
        "token_type": "Bearer",
        "account_subject": {"iss": ISSUER, "sub": SUBJECT},
    }
    found.update(overrides)
    return {k: v for k, v in found.items() if v is not None}


def test_the_link_url_carries_an_s256_challenge_and_never_the_verifier() -> None:
    verifier, state = new_verifier(), new_state()
    assert len(verifier) == 43
    assert verifier != new_verifier()
    url = link_url(
        player_base_url="https://player.example/",
        redirect_uri=callback_uri(ISSUER),
        state=state,
        verifier=verifier,
        login_hint=SUBJECT,
    )
    parts = urlsplit(url)
    assert (parts.netloc, parts.path) == ("player.example", "/link/source")
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert query == {
        "source": "classbeat",
        "redirect_uri": "https://classbeat.test/connections/player/callback",
        "state": state,
        "code_challenge": challenge_of(verifier),
        "login_hint": SUBJECT,
    }
    assert verifier not in url


def test_the_rfc_7636_example_challenge() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge_of(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.mark.parametrize(
    ("kind", "value", "ok"),
    [
        ("lc", "cbp_lc_" + "a" * 43, True),
        ("src", SOURCE, True),
        ("lc", SOURCE, False),
        ("src", "cbp_src_" + "a" * 42, False),
        ("src", "cbp_src_" + "a" * 44, False),
        ("src", "cbp_src_" + "a" * 42 + "=", False),
        ("src", " " + SOURCE, False),
        ("src", None, False),
        ("src", 7, False),
    ],
)
def test_token_shapes(kind: Any, value: object, ok: bool) -> None:
    assert is_token(kind, value) is ok


def test_the_own_subject_keeps_the_link() -> None:
    decision = decide(answer(), issuer=ISSUER, subject=SUBJECT)
    assert (decision.action, decision.token, decision.link_id) == ("keep", SOURCE, None)
    assert SOURCE not in repr(decision)
    assert SOURCE not in str(decision)


@pytest.mark.parametrize(
    "changed",
    [
        {"account_subject": {"iss": ISSUER, "sub": "someone-else"}},
        {"account_subject": {"iss": "https://evil.example", "sub": SUBJECT}},
        {"account_subject": {"iss": ISSUER + "/", "sub": SUBJECT}},
        {"account_subject": {"iss": ISSUER}},
        {"account_subject": {"sub": SUBJECT}},
        {"account_subject": {"iss": ISSUER, "sub": [SUBJECT]}},
        {"account_subject": [ISSUER, SUBJECT]},
        {"account_subject": None},
        {"token_type": "mac"},
        {"token_type": None},
    ],
)
def test_anything_else_ends_the_link(changed: dict[str, Any]) -> None:
    decision = decide(answer(**changed), issuer=ISSUER, subject=SUBJECT)
    assert (decision.action, decision.token) == ("end", SOURCE)


@pytest.mark.parametrize("token", ["cbp_lc_" + "a" * 43, "x", None, 3])
def test_no_usable_token_is_invalid(token: object) -> None:
    found = answer()
    found["source_token"] = token
    assert decide(found, issuer=ISSUER, subject=SUBJECT).action == "invalid"


@pytest.mark.parametrize(
    ("link_id", "kept"),
    [("lnk_01", "lnk_01"), ("a" * 64, "a" * 64), ("a" * 65, None), ("a/b", None), (5, None)],
)
def test_a_link_id_is_kept_only_when_well_formed(link_id: object, kept: str | None) -> None:
    assert decide(answer(link_id=link_id), issuer=ISSUER, subject=SUBJECT).link_id == kept
