"""ID-token validation against the fake provider (ADR 0003 section 5): no database, no network."""

import time
from typing import Any

import pytest

from classbeat.auth.oauth_idtoken import IdTokenError, JwksCache, verify_id_token
from classbeat.auth.oauth_providers import Provider, enabled_providers
from classbeat.config import Settings
from oidcfake import DROP, GOOGLE_JWKS, MICROSOFT_JWKS, TENANT, FakeProviders

NONCE = "the-nonce"
URL = "https://x/auth?client_id={cid}&nonce=" + NONCE


@pytest.fixture
def providers(fake: FakeProviders) -> dict[str, Provider]:
    values: dict[str, Any] = {}
    for name, cid in fake.client_ids.items():
        values[f"oauth_{name}_client_id"] = cid
        values[f"oauth_{name}_client_secret"] = "s"
    return enabled_providers(Settings(**values))


async def fetch(fake: FakeProviders):
    async def inner(url: str) -> dict[str, Any]:
        fake.jwks_fetches += 1
        return {"keys": [k.as_dict(private=False) for k in fake.published]}

    return inner


async def verify(
    fake: FakeProviders, provider: Provider, token: str, cache: JwksCache | None = None
):
    cache = cache or JwksCache(await fetch(fake))
    return await verify_id_token(provider, token, nonce=NONCE, cache=cache)


def token(fake: FakeProviders, provider: Provider, **kwargs: Any) -> str:
    sign_args = {k: kwargs.pop(k) for k in ("alg", "key", "kid") if k in kwargs}
    url = URL.format(cid=provider.client_id)
    kwargs.setdefault("nonce", NONCE)
    return fake.sign(fake.claims_for(provider.name, url, **kwargs), **sign_args)


async def test_a_good_google_token_is_accepted(fake: FakeProviders, providers) -> None:
    claims = await verify(fake, providers["google"], token(fake, providers["google"]))
    assert (claims.subject, claims.email, claims.email_verified) == ("sub-1", "g@example.com", True)


async def test_microsoft_subject_is_tid_slash_oid_and_the_email_is_never_verified(
    fake: FakeProviders, providers
) -> None:
    claims = await verify(fake, providers["microsoft"], token(fake, providers["microsoft"]))
    assert claims.subject == f"{TENANT}/oid-1"
    assert claims.email_verified is False


@pytest.mark.parametrize(
    ("name", "override"),
    [
        ("aud", {"aud": "someone-else"}),
        ("iss", {"iss": "https://evil.example"}),
        ("nonce", {"nonce": "other"}),
        ("nonce missing", {"nonce": DROP}),
        ("expired", {"exp": int(time.time()) - 3600}),
        ("iat in the future", {"iat": int(time.time()) + 3600}),
        ("exp missing", {"exp": DROP}),
        ("sub missing", {"sub": DROP}),
    ],
)
async def test_bad_claims_are_refused(fake: FakeProviders, providers, name: str, override) -> None:
    with pytest.raises(IdTokenError):
        await verify(fake, providers["google"], token(fake, providers["google"], **override))


async def test_microsoft_issuer_must_match_the_tokens_own_tenant(fake, providers) -> None:
    other = {"iss": "https://login.microsoftonline.com/another-tenant/v2.0"}
    with pytest.raises(IdTokenError, match="tenant"):
        await verify(fake, providers["microsoft"], token(fake, providers["microsoft"], **other))
    with pytest.raises(IdTokenError, match="tid"):
        await verify(fake, providers["microsoft"], token(fake, providers["microsoft"], oid=DROP))


@pytest.mark.parametrize("alg", ["none", "HS256"])
async def test_only_rs256_is_accepted(fake: FakeProviders, providers, alg: str) -> None:
    with pytest.raises(IdTokenError, match="RS256"):
        await verify(fake, providers["google"], token(fake, providers["google"], alg=alg))


async def test_a_token_signed_with_another_key_is_refused(fake: FakeProviders, providers) -> None:
    with pytest.raises(IdTokenError, match="signature"):
        await verify(fake, providers["google"], token(fake, providers["google"], key=fake.other))


async def test_leeway_is_60_seconds(fake: FakeProviders, providers) -> None:
    now = int(time.time())
    inside = token(fake, providers["google"], exp=now - 30, iat=now - 600)
    assert (await verify(fake, providers["google"], inside)).subject == "sub-1"
    outside = token(fake, providers["google"], exp=now - 120, iat=now - 600)
    with pytest.raises(IdTokenError, match="expired"):
        await verify(fake, providers["google"], outside)
    skewed = token(fake, providers["google"], iat=now + 30)
    assert (await verify(fake, providers["google"], skewed)).subject == "sub-1"


@pytest.mark.parametrize(
    ("override", "verified"),
    [
        ({"email_verified": False}, False),
        ({"email_verified": DROP}, False),
        ({"email": DROP}, False),
        ({"email_verified": "true"}, True),
    ],
)
async def test_google_email_counts_as_verified_only_when_stated(
    fake: FakeProviders, providers, override, verified: bool
) -> None:
    claims = await verify(fake, providers["google"], token(fake, providers["google"], **override))
    assert claims.email_verified is verified


async def test_jwks_is_refetched_on_an_unknown_kid_at_most_once_a_minute(
    fake: FakeProviders, providers
) -> None:
    now = [1000.0]
    cache = JwksCache(await fetch(fake), clock=lambda: now[0])
    google = providers["google"]
    await verify(fake, google, token(fake, google), cache)
    await verify(fake, google, token(fake, google), cache)
    assert fake.jwks_fetches == 1  # a known kid is served from the cache
    rotated = token(fake, google, key=fake.rotated)  # the provider rotated, not yet published
    with pytest.raises(IdTokenError, match="rate limit"):
        await verify(fake, google, rotated, cache)  # within a minute of the last fetch
    assert fake.jwks_fetches == 1
    now[0] += 61
    for _ in range(3):
        with pytest.raises(IdTokenError, match="unknown key id"):
            await verify(fake, google, rotated, cache)
    assert fake.jwks_fetches == 2  # one refetch, then rate limited
    fake.published.append(fake.rotated)
    with pytest.raises(IdTokenError, match="rate limit"):
        await verify(fake, google, rotated, cache)
    assert fake.jwks_fetches == 2
    now[0] += 61
    assert (await verify(fake, google, rotated, cache)).subject == "sub-1"
    assert fake.jwks_fetches == 3


def test_the_jwks_urls_are_the_providers_own() -> None:
    assert GOOGLE_JWKS.startswith("https://www.googleapis.com/")
    assert MICROSOFT_JWKS.startswith("https://login.microsoftonline.com/")
