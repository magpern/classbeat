"""The provider's pure rules: requests, auth_time, the key set and settings (ADR 0034 s. 1)."""

from datetime import UTC, datetime, timedelta

import pytest

from classbeat.auth.oidc import keyset_policy as ks
from classbeat.auth.oidc.auth_time_policy import SessionTimes, decide
from classbeat.auth.oidc.request_policy import (
    AuthorizeRequest,
    Client,
    ErrorPage,
    RedirectError,
    check_authorize,
)
from classbeat.config_features import check_player_client_id as check_client_id
from classbeat.config_features import check_player_redirect_uri as check_redirect_uri

CLIENT = Client("player", "https://player.example/cb")
GOOD = {
    "response_type": ["code"],
    "client_id": ["player"],
    "redirect_uri": ["https://player.example/cb"],
    "scope": ["openid email"],
    "state": ["s1"],
    "nonce": ["n1"],
    "code_challenge": ["a" * 43],
    "code_challenge_method": ["S256"],
}
T0 = datetime(2026, 11, 15, 12, tzinfo=UTC)


def with_(**changes: list[str] | None) -> dict[str, list[str]]:
    out = {**GOOD, **{k: v for k, v in changes.items() if v is not None}}
    return {k: v for k, v in out.items() if changes.get(k, "keep") is not None}


def test_a_good_request_is_accepted() -> None:
    assert check_authorize(GOOD, CLIENT) == AuthorizeRequest(
        "https://player.example/cb", "s1", "n1", "a" * 43, prompt_login=False
    )
    login = check_authorize(with_(prompt=["login"]), CLIENT)
    assert isinstance(login, AuthorizeRequest)
    assert login.prompt_login
    assert isinstance(check_authorize(with_(scope=["openid"]), CLIENT), AuthorizeRequest)


@pytest.mark.parametrize(
    "changes",
    [
        {"client_id": ["x"]},
        {"client_id": None},
        {"client_id": ["player", "player"]},
        {"redirect_uri": ["https://player.example/cb?x=1"]},
        {"redirect_uri": ["https://player.example/cb", "https://player.example/cb"]},
    ],
)
def test_a_wrong_client_or_address_is_a_page(changes: dict[str, list[str] | None]) -> None:
    assert isinstance(check_authorize(with_(**changes), CLIENT), ErrorPage)


@pytest.mark.parametrize(
    "changes",
    [
        {"prompt": ["none"]},
        {"prompt": ["consent"]},
        {"prompt": ["select_account"]},
        {"prompt": ["login none"]},
        {"prompt": ["login", "login"]},
        {"max_age": ["300"]},
        {"scope": ["openid  email"]},
        {"scope": ["openid offline_access"]},
        {"nonce": ["n" * 513]},
        {"nonce": ["n\x00"]},
        {"code_challenge": ["a" * 42]},
        {"code_challenge": ["a" * 42 + "="]},
        {"state": None},
    ],
)
def test_everything_else_is_invalid_request(changes: dict[str, list[str] | None]) -> None:
    outcome = check_authorize(with_(**changes), CLIENT)
    assert isinstance(outcome, RedirectError)
    assert outcome.error == "invalid_request"
    assert outcome.state == (None if "state" in changes else "s1")


def times(**kw: datetime | bool | None) -> SessionTimes:
    values: dict[str, object] = {
        "signed_in_at": T0, "reauth_at": T0, "stepup_at": None, "totp_enabled": False, **kw
    }  # fmt: skip
    return SessionTimes(**values)  # pyright: ignore[reportArgumentType]


def test_auth_time_without_prompt_login_is_the_latest_authentication() -> None:
    later = T0 + timedelta(minutes=5)
    decision = decide(
        times(reauth_at=later), prompt_login=False, received_at=T0, can_reauthenticate=True
    )
    assert (decision.auth_time, decision.missing) == (later, None)


def test_prompt_login_needs_reauth_after_the_request_and_stepup_with_totp() -> None:
    after = T0 + timedelta(seconds=1)
    args = {"prompt_login": True, "received_at": T0}
    assert decide(times(), **args, can_reauthenticate=True).missing == "reauth_required"
    assert decide(times(), **args, can_reauthenticate=False).missing == "login_required"
    ok = decide(times(reauth_at=after), **args, can_reauthenticate=True)
    assert (ok.auth_time, ok.missing) == (after, None)
    totp = times(reauth_at=after, totp_enabled=True, stepup_at=T0)
    assert decide(totp, **args, can_reauthenticate=True).missing == "stepup_required"
    stepped = times(reauth_at=after, totp_enabled=True, stepup_at=after)
    assert decide(stepped, **args, can_reauthenticate=True).missing is None


PEM = "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n"


def test_add_next_promote_and_retire_follow_the_publish_windows() -> None:
    keys = ks.add_next([], "k1", PEM, T0)
    assert [(k.kid, k.state) for k in keys] == [("k1", "current")]
    keys = ks.add_next(keys, "k2", PEM, T0)
    with pytest.raises(ks.KeysetRefused, match="next key exists"):
        ks.add_next(keys, "k3", PEM, T0)
    with pytest.raises(ks.KeysetRefused, match="24 hours and 5 minutes"):
        ks.promote(keys, T0 + timedelta(hours=24, minutes=4))
    promoted_at = T0 + ks.PUBLISH_BEFORE_SIGNING
    keys = ks.promote(keys, promoted_at)
    assert [(k.kid, k.state) for k in keys] == [("k1", "retired"), ("k2", "current")]
    assert ks.signing_key(keys).kid == "k2"
    # The retired key stays published for 24 hours after its last signature.
    assert [k.kid for k in ks.published(keys, promoted_at + timedelta(hours=23))] == ["k1", "k2"]
    assert [k.kid for k in ks.drop_expired(keys, promoted_at + timedelta(hours=23))] == [
        "k1", "k2"
    ]  # fmt: skip
    assert [k.kid for k in ks.drop_expired(keys, promoted_at + timedelta(hours=24))] == ["k2"]
    assert ks.parse(ks.dump(keys)) == keys


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"keys": []}',
        '{"keys": [{"kid": "a b", "state": "current", "published_at": "2026-01-01T00:00:00+00:00",'
        ' "retired_at": null, "pem": "-----BEGIN"}]}',
        '{"keys": [{"kid": "a", "state": "current", "published_at": "2026-01-01T00:00:00",'
        ' "retired_at": null, "pem": "-----BEGIN"}]}',
        '{"keys": [{"kid": "a", "state": "next", "published_at": "2026-01-01T00:00:00+00:00",'
        ' "retired_at": null, "pem": "-----BEGIN"}]}',
    ],
)
def test_a_bad_key_set_is_refused(text: str) -> None:
    with pytest.raises(ks.KeysetRefused):
        ks.parse(text)


def test_a_key_entry_repr_never_shows_the_key() -> None:
    entry = ks.add_next([], "k1", PEM, T0)[0]
    assert "PRIVATE" not in repr(entry)
    assert "PRIVATE" not in repr([entry])


@pytest.mark.parametrize("value", ["classbeat-player", "a.b_c-1"])
def test_good_client_ids(value: str) -> None:
    assert check_client_id(value) == value


@pytest.mark.parametrize("value", ["", "a b", "x" * 129, "é"])
def test_bad_client_ids(value: str) -> None:
    with pytest.raises(ValueError, match="oidc_player_client_id"):
        check_client_id(value)


@pytest.mark.parametrize(
    "value", ["https://player.example/cb", "http://127.0.0.1:8080/cb", "http://localhost/cb"]
)
def test_good_redirect_addresses(value: str) -> None:
    assert check_redirect_uri(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "http://player.example/cb",
        "https://player.example/cb#x",
        "https://user@player.example/cb",
        "ftp://player.example/cb",
        "https:///cb",
        "https://player.example/" + "x" * 512,
    ],
)
def test_bad_redirect_addresses(value: str) -> None:
    with pytest.raises(ValueError, match="oidc_player_redirect_uri"):
        check_redirect_uri(value)
