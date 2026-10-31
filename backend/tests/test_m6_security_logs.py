"""M6 security suite, log secrecy over the full fake flows (roadmap M6, issue #339; ADR 0003
section 10, ADR 0004 section 7, T7 and T41). Fakes only, no network.

``m6world.full_flows`` plays sign-in (a wrong password first), a password reset by mail, OAuth
sign-up (client secret, code, state), MOWL and Spotify connect (a refusal first each), a song
pick, its approval, the design run, Save, and an admin session with TOTP, in one running app with
the web and worker sides in one process. Log capture is on for all of it at DEBUG, as production
configures logging (``configure_logging``), plus every record's raw attributes and unredacted
traceback. Afterwards no secret it made or used appears, in any form (plain, URL-encoded, base64,
hex), in any log record or in any error response; and no credential that is never returned
appears in any response at all.
"""

import pytest
from sqlalchemy.engine import Connection

from m6world import Capture, Known, World, capture, full_flows, leaks, spot, world
from spotifytools import spotify_admin

__all__ = ["spot", "spotify_admin", "world"]  # fixtures


async def run(
    world: World, admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> tuple[Known, Capture]:
    known = Known()
    with capture(monkeypatch) as cap:
        await full_flows(world, admin, known)
    return known, cap


async def test_no_secret_is_in_any_log_record(
    world: World, spotify_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    known, cap = await run(world, spotify_admin, monkeypatch)
    assert cap.log_count() > 50  # the capture saw the flows (not vacuous)
    assert len(known.values) >= 25
    assert any(n.startswith("spotify_code_verifier") for n in known.values)
    assert any(n.startswith("oidc_code_verifier") for n in known.values)
    logged = cap.logs()
    assert "POST /api/connections/mowl" in logged or "/api/connections/mowl" in logged
    assert "/api/connections/swedebeat" in logged
    swedebeat = {"swedebeat_password", "swedebeat_token", "swedebeat_device_id"}
    assert swedebeat | {"swedebeat_app_key", "swedebeat_app_iv"} <= set(known.values)
    assert leaks(logged, known) == []


async def test_no_secret_is_in_any_error_response(
    world: World, spotify_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    known, cap = await run(world, spotify_admin, monkeypatch)
    errors = cap.error_responses()
    assert len(errors) >= 12  # the wrong passwords, 403s, 404s ... were captured
    problems: list[str] = []
    for seen in errors:
        shown = seen.body + b"\n".join(f"{k}: {v}".encode() for k, v in seen.headers)
        if found := leaks(shown, known):
            problems.append(f"{seen.method} {seen.path} {seen.status}: {found}")
    assert problems == []


async def test_credentials_that_are_never_returned_are_in_no_response(
    world: World, spotify_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    known, cap = await run(world, spotify_admin, monkeypatch)
    assert len(cap.responses) > 55
    names = known.never_returned
    problems: list[str] = []
    for seen in cap.responses:
        headers = b"\n".join(f"{k}: {v}".encode() for k, v in seen.headers if k != "set-cookie")
        if found := leaks(seen.body + headers, known, names):
            problems.append(f"{seen.method} {seen.path} {seen.status}: {found}")
    assert problems == []
