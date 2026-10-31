"""Checks of the backup and restore drill that need the application's own code.

Run inside a stack's web container by scripts/backup-restore-drill.sh, the script piped to
stdin so no mount is needed::

    docker compose exec -T web python - <command> [args] < scripts/drill/probe.py

It never runs against a real deployment: the drill only starts it in its own throwaway projects.
Passwords and tokens are throwaway values the drill generates (environment DRILL_PASSWORD and
DRILL_TOKEN); the real key files are never printed. Exit code 0 means the check held.

Commands:
  seed EMAIL...       create the users (password DRILL_PASSWORD) with a MOWL connection each,
                      encrypted under the stack's KEK (token DRILL_TOKEN-<email>)
  decrypt EMAIL...    the right key decrypts every connection to the seeded token
  rls EMAIL...        row level security still isolates: no user, no rows; own rows only
  signin EMAIL        POST /api/auth/login with DRILL_PASSWORD, then GET /api/auth/session
  badsignin EMAIL     a wrong password is refused (401) and no session starts
  wrongkey-same-id    a different key under the current key id is refused at startup
  wrongkey-other-id   a different key under another id starts, but cannot read the old rows
  wrongkey-unchecked  a different key used without the startup check fails to decrypt
"""

import asyncio
import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy import text

from classbeat.auth import PasswordHasher, normalize_password
from classbeat.auth.accounts import create_user
from classbeat.auth.deps import issue_context
from classbeat.config import Settings
from classbeat.connections import ConnectionFailure, open_connection, save
from classbeat.crypto import keks
from classbeat.crypto.envelope import Kek
from classbeat.db import create_engine, create_session_factory, system_session, user_session


def token_for(email: str) -> str:
    return f"{os.environ['DRILL_TOKEN']}-{email}"


def fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


async def context(factory, email: str):
    async with system_session(factory) as db:
        row = (
            await db.execute(
                text("SELECT id FROM users WHERE email_normalized = :e"), {"e": email.lower()}
            )
        ).first()
    if row is None:
        raise SystemExit(f"FAIL: no user {email}")
    return await issue_context(factory, row[0], "user")


async def seed(factory, emails: list[str]) -> int:
    hasher = PasswordHasher(workers=1)
    ring = await keks.load_keyring(factory, Settings().connection_kek_id)
    password = normalize_password(os.environ["DRILL_PASSWORD"])
    for email in emails:
        async with system_session(factory) as db:
            await create_user(db, email, password_hash=await hasher.hash(password))
        ctx = await context(factory, email)
        await save(factory, ring, ctx, "mowl", {"token": token_for(email)})
        print(f"seeded {email} with key id {ring.current_id}")
    return 0


async def decrypt(factory, emails: list[str]) -> int:
    ring = await keks.load_keyring(factory, Settings().connection_kek_id)
    for email in emails:
        opened = await open_connection(factory, ring, await context(factory, email), "mowl")
        if opened.credentials.mowl_token != token_for(email):
            return fail(f"{email}: the decrypted token is not the seeded one")
        print(f"decrypted {email}: token matches (key id {ring.current_id})")
    return 0


async def rls(factory, emails: list[str]) -> int:
    async with system_session(factory) as db:
        if (await db.execute(text("SELECT count(*) FROM connections"))).scalar_one() != 0:
            return fail("a session without a user can read connection rows")
    print("no user: 0 connection rows")
    contexts = {email: await context(factory, email) for email in emails}
    for email, ctx in contexts.items():
        async with user_session(factory, ctx) as db:
            owners = (await db.execute(text("SELECT DISTINCT owner_id FROM connections"))).all()
            if [row[0] for row in owners] != [ctx.user_id]:
                return fail(f"{email} sees rows that are not theirs (or none)")
            others = [c.user_id for e, c in contexts.items() if e != email]
            leaked = (
                await db.execute(
                    text("SELECT count(*) FROM connections WHERE owner_id = ANY(:o)"),
                    {"o": others},
                )
            ).scalar_one()
            if leaked:
                return fail(f"{email} can read another user's connection by owner_id")
        print(f"{email}: sees only their own rows")
    return 0


def http(method: str, url: str, headers: dict[str, str], body: bytes | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return response.status, response.read(), response.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers.get_all("Set-Cookie") or []


def cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def remember(jar: dict[str, str], set_cookies: list[str]) -> None:
    # The cookies are Secure and the drill speaks plain http to localhost: keep them by hand.
    for line in set_cookies:
        name, _, value = line.split(";", 1)[0].partition("=")
        jar[name.strip()] = value.strip()


def signin(email: str, password: str, expect_ok: bool) -> int:
    origin = os.environ["CLASSBEAT_PUBLIC_BASE_URL"].rstrip("/")
    base = "http://127.0.0.1:8000"
    jar: dict[str, str] = {}
    _, body, cookies = http("GET", f"{base}/api/auth/session", {"Origin": origin})
    remember(jar, cookies)
    headers = {
        "Origin": origin,
        "X-CSRF-Token": json.loads(body)["csrf_token"],
        "Content-Type": "application/json",
        "Cookie": cookie_header(jar),
    }
    payload = json.dumps({"email": email, "password": password}).encode()
    status, _, cookies = http("POST", f"{base}/api/auth/login", headers, payload)
    remember(jar, cookies)
    if not expect_ok:
        if status != 401:
            return fail(f"a wrong password gave HTTP {status}, not 401")
        print("wrong password refused (401)")
        return 0
    if status != 200:
        return fail(f"sign-in gave HTTP {status}")
    _, body, _ = http(
        "GET", f"{base}/api/auth/session", {"Origin": origin, "Cookie": cookie_header(jar)}
    )
    state = json.loads(body)
    if not state.get("authenticated") or state["user"]["email"].lower() != email.lower():
        return fail("the session after sign-in is not authenticated as the user")
    print(f"signed in as {email}; session is authenticated")
    return 0


def random_key_dir() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="drill-wrong-key-"))
    (directory / "connection_kek").write_text(secrets.token_hex(32) + "\n")
    return directory


async def wrongkey_same_id(factory, emails: list[str]) -> int:
    try:
        await keks.load_keyring(
            factory, Settings().connection_kek_id, directory=random_key_dir()
        )
    except keks.KekError as error:
        print(f"refused at startup, as it must be: {error}")
        return 0
    return fail("a different key under the same key id was accepted")


async def wrongkey_other_id(factory, emails: list[str]) -> int:
    ring = await keks.load_keyring(factory, "kek-drill-wrong", directory=random_key_dir())
    try:
        await open_connection(factory, ring, await context(factory, emails[0]), "mowl")
    except ConnectionFailure as error:
        if error.args == ("key_unavailable",):
            print("the old rows are unreadable without their key: key_unavailable")
            return 0
        return fail(f"unexpected category {error.args}")
    return fail("a wrong key opened the connection")


async def wrongkey_unchecked(factory, emails: list[str]) -> int:
    wrong = bytes.fromhex((random_key_dir() / "connection_kek").read_text().strip())
    ring = keks.KeyRing(Kek(Settings().connection_kek_id, wrong), Path(tempfile.mkdtemp()))
    try:
        await open_connection(factory, ring, await context(factory, emails[0]), "mowl")
    except ConnectionFailure as error:
        if error.args == ("decrypt_failed",):
            print("a wrong key under the right id fails authentication: decrypt_failed")
            return 0
        return fail(f"unexpected category {error.args}")
    return fail("a wrong key decrypted the connection")


async def main(argv: list[str]) -> int:
    command, rest = argv[0], argv[1:]
    if command in ("signin", "badsignin"):
        password = os.environ["DRILL_PASSWORD"]
        good = command == "signin"
        return signin(rest[0], password if good else password + "-wrong", good)
    engine = create_engine(Settings())
    try:
        handlers = {
            "seed": seed,
            "decrypt": decrypt,
            "rls": rls,
            "wrongkey-same-id": wrongkey_same_id,
            "wrongkey-other-id": wrongkey_other_id,
            "wrongkey-unchecked": wrongkey_unchecked,
        }
        return await handlers[command](create_session_factory(engine), rest)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
