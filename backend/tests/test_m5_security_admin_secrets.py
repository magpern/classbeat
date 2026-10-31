"""M5 security suite, part 2 (#227): a planted value of every secret-bearing column of ADR 0011
section 5's list (``maint_guard.SECRET_COLUMNS``) never appears in any admin response, the audit
view's included (PR #277 review; ``class_playlists`` added on the #303 review). The routes are
enumerated from the app, with ``test_m5_security_admin.py``'s valid requests.

Threats covered: T91.
"""

import base64
import json
import secrets
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from test_m5_security_admin import (
    BODIES,
    admin,
    env,
    fill,
    routes,
    send,
    target_with_entitlement,
)

__all__ = ["env"]  # fixtures


# --- planted secrets never leave through an admin route ---------------------------------------


def plant_secrets(conn: Connection, owner: uuid.UUID) -> list[str]:
    """One row of every table with a secret column (``maint_guard.SECRET_COLUMNS``) that a
    user's account can hold, each secret column a distinct planted value; the needles to look
    for (texts as they are, bytes as hex, base64 and latin-1 text)."""
    raw = {k: secrets.token_bytes(n) for k, n in (
        ("c_secret", 40), ("c_nonce", 12), ("c_wrapped", 48), ("c_dek_nonce", 12),
        ("t_secret", 32), ("t_nonce", 12), ("t_wrapped", 48), ("t_dek_nonce", 12),
        ("recovery", 32), ("rev_hash", 32), ("op_hash", 32), ("pool_hash", 32), ("flow", 32),
        ("state", 32), ("sp_state", 32), ("sp_session", 32), ("device", 32), ("token", 32),
        ("job_hash", 32), ("sw_content", 32), ("sw_state", 32), ("cp_content", 32),
    )}  # fmt: skip
    tag = secrets.token_hex(5)
    words = {
        "password": f"$argon2id$v=19$PLANTEDpw{tag}",
        "c_key": f"kek-plantedc{tag}",
        "t_key": f"kek-plantedt{tag}",
        "client_id": secrets.token_hex(16),
        "title": f"PLANTED title {tag}",
        "brief": f"PLANTED brief {tag}",
        "content": f"PLANTED revision {tag}",
        "music": f"PLANTED music {tag}",
        "provenance": f"PLANTED provenance {tag}",
        "message": f"PLANTED message {tag}",
        "change": f"PLANTED change {tag}",
        "op_key": f"PLANTEDopkey{tag}",
        "external": f"PLANTEDexternal{tag}",
        "result": f"PLANTED result {tag}",
        "context": f"PLANTED context {tag}",
        "spotify_user": f"planted-spotify-{tag}",
        "pool_playlist": f"Pool{tag}PLANTED{secrets.token_hex(4)}"[:22].ljust(22, "x"),
        "subject": f"planted-subject-{tag}",
        "email_at_link": f"planted-link-{tag}@example.org",
        "oauth_nonce": f"PLANTEDnonce{tag}",
        "pkce": f"PLANTEDpkce{tag}",
        "return_to": f"/planted/return/{tag}",
        "sp_client": secrets.token_hex(16),
        "redirect": f"http://127.0.0.1/planted/{tag}",
        "verifier": (f"PLANTEDverifier{tag}" + "v" * 43)[:43],
        "auth_ip": f"203.0.113.{secrets.randbelow(200) + 1}-{tag}",
        "auth_detail": f"PLANTED detail {tag}",
        "session_ip": f"198.51.100.{secrets.randbelow(200) + 1}",
        "user_agent": f"PLANTED-agent/{tag}",
        "job_input": f"PLANTED job input {tag}",
        "job_key": f"PLANTEDjobkey{tag}",
        "event": f"PLANTED event {tag}",
        "sw_name": f"PLANTED workout {tag}",
        "sw_program": str(900_000_000_000 + secrets.randbelow(10**9)),
        "link_source": f"Link{tag}PLANTED{secrets.token_hex(4)}"[:22].ljust(22, "y"),
        "link_playlist": str(800_000_000_000 + secrets.randbelow(10**9)),
        "cp_playlist": f"Cp{tag}PLANTED{secrets.token_hex(4)}"[:22].ljust(22, "z"),
        "cp_spotify_user": f"planted-cp-user-{tag}",
        "cp_name": f"PLANTED class playlist {tag}",
    }
    class_id = uuid.uuid4()

    def run(sql: str, **params: Any) -> Any:
        return conn.execute(text(sql), {"o": owner, "c": class_id, **params})

    def js(value: str) -> str:
        return json.dumps({"x": value})

    run("UPDATE users SET password_hash = :p WHERE id = :o", p=words["password"])
    run("UPDATE sessions SET ip = :i, user_agent = :a WHERE user_id = :o",
        i=words["session_ip"], a=words["user_agent"])  # fmt: skip
    run("INSERT INTO connections (owner_id, provider, status, public, secret, secret_nonce, "
        "wrapped_dek, dek_nonce, key_id) VALUES (:o, 'spotify', 'connected', CAST(:p AS jsonb), "
        ":s, :n, :w, :d, :k)", p=json.dumps({"client_id": words["client_id"]}),
        s=raw["c_secret"], n=raw["c_nonce"], w=raw["c_wrapped"], d=raw["c_dek_nonce"],
        k=words["c_key"])  # fmt: skip
    run("INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, wrapped_dek, dek_nonce) "
        "VALUES (:o, :s, :n, :k, :w, :d)", s=raw["t_secret"], n=raw["t_nonce"], k=words["t_key"],
        w=raw["t_wrapped"], d=raw["t_dek_nonce"])  # fmt: skip
    run("INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (:o, :h)", h=raw["recovery"])
    run("INSERT INTO classes (id, owner_id, title, brief) VALUES (:c, :o, :t, CAST(:b AS jsonb))",
        t=words["title"], b=js(words["brief"]))  # fmt: skip
    run("INSERT INTO class_revisions (owner_id, class_id, number, kind, content, content_hash, "
        "music, provenance) VALUES (:o, :c, 1, 'draft', CAST(:a AS jsonb), :h, CAST(:m AS jsonb), "
        "CAST(:p AS jsonb))", a=js(words["content"]), h=raw["rev_hash"], m=js(words["music"]),
        p=js(words["provenance"]))  # fmt: skip
    run("INSERT INTO class_messages (owner_id, class_id, seq, role, body, change) "
        "VALUES (:o, :c, 1, 'user', :b, CAST(:x AS jsonb))", b=words["message"],
        x=js(words["change"]))  # fmt: skip
    job = run("INSERT INTO jobs (owner_id, kind, state, class_id, input, idempotency_key, "
              "request_hash, credit_state) VALUES (:o, 'test_kind', 'queued', :c, "
              "CAST(:i AS jsonb), :k, :h, 'none') RETURNING id", i=js(words["job_input"]),
              k=words["job_key"], h=raw["job_hash"]).scalar_one()  # fmt: skip
    run(
        "INSERT INTO job_events (owner_id, job_id, seq, kind, payload, lease_version) "
        "VALUES (:o, :j, 1, 'queued', CAST(:p AS jsonb), 0)",
        j=job,
        p=js(words["event"]),
    )
    run("INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, state, "
        "external_id, result, request_context, lease_version) VALUES (:o, "
        "'spotify_create_playlist', :k, :h, 'succeeded', :e, CAST(:r AS jsonb), "
        "CAST(:x AS jsonb), 0)", k=words["op_key"], h=raw["op_hash"], e=words["external"],
        r=js(words["result"]), x=js(words["context"]))  # fmt: skip
    run("INSERT INTO spotify_pool (owner_id, spotify_user_id, playlist_id, consent_version, "
        "last_hash) VALUES (:o, :u, :p, 1, :h)", u=words["spotify_user"],
        p=words["pool_playlist"], h=raw["pool_hash"])  # fmt: skip
    run(
        "INSERT INTO identities (user_id, provider, subject, email_at_link) "
        "VALUES (:o, 'google', :s, :e)",
        s=words["subject"],
        e=words["email_at_link"],
    )
    run("INSERT INTO oauth_flows (flow_hash, provider, purpose, state_hash, nonce, pkce_verifier, "
        "user_id, return_to, expires_at) VALUES (:f, 'google', 'link', :s, :n, :p, :o, :r, "
        "now() + interval '5 minutes')", f=raw["flow"], s=raw["state"], n=words["oauth_nonce"],
        p=words["pkce"], r=words["return_to"])  # fmt: skip
    run("INSERT INTO spotify_oauth_flows (owner_id, state_hash, session_hash, client_id, "
        "redirect_uri, verifier, expires_at) VALUES (:o, :s, :h, :i, :r, :v, "
        "now() + interval '5 minutes')", s=raw["sp_state"], h=raw["sp_session"],
        i=words["sp_client"], r=words["redirect"], v=words["verifier"])  # fmt: skip
    run("INSERT INTO known_devices (user_id, device_hash) VALUES (:o, :h)", h=raw["device"])
    run(
        "INSERT INTO auth_tokens (purpose, token_hash, user_id, expires_at) VALUES "
        "('reset_password', :h, :o, now() + interval '1 hour')",
        h=raw["token"],
    )
    run(
        "INSERT INTO auth_events (kind, user_id, ip, detail) VALUES ('sign_in', :o, :i, "
        "CAST(:d AS jsonb))",
        i=words["auth_ip"],
        d=js(words["auth_detail"]),
    )
    run("INSERT INTO saved_workouts (owner_id, class_id, program_id, revision_number, "
        "content_hash, state_hash, name) VALUES (:o, :c, :p, 1, :h, :s, :n)",
        p=int(words["sw_program"]), h=raw["sw_content"], s=raw["sw_state"],
        n=words["sw_name"])  # fmt: skip
    run("INSERT INTO class_playlists (owner_id, class_id, playlist_id, spotify_user_id, name, "
        "state, content_hash) VALUES (:o, :c, :p, :s, :n, 'created', :h)",
        p=words["cp_playlist"], s=words["cp_spotify_user"], n=words["cp_name"],
        h=raw["cp_content"])  # fmt: skip
    run("INSERT INTO mowl_playlist_links (owner_id, source, source_id, playlist_id) "
        "VALUES (:o, 'spotify', :s, :p)", s=words["link_source"],
        p=int(words["link_playlist"]))  # fmt: skip
    needles = list(words.values())
    for value in raw.values():
        needles += [value.hex(), base64.b64encode(value).decode(), value.decode("latin-1")]
    return needles


async def test_no_admin_route_returns_a_planted_secret_of_any_table(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """PR #277 review: every secret-bearing column (non-zero nonces and wrapped keys, key ids,
    TOTP and recovery codes, revisions, messages, external operations, the Spotify pool,
    identities, both OAuth flow tables, devices, tokens, events, saved workouts, class
    playlists and links) is
    planted for one user; no response of any admin route contains one, the audit view
    included (which shows every call's params).

    Threats: T91.
    """
    actor = await admin(env, make_actor)
    values = await target_with_entitlement(env, superuser)
    owner = uuid.UUID(values["user_id"])
    rider = await make_actor(owner)  # a session row to plant ip and user agent into
    needles = [*plant_secrets(superuser, owner), rider.token, actor.token]
    responses = []
    for method, route in routes(env):
        path = fill(route, values)
        if route.path.endswith("/users"):
            path += "?q=rider"
        responses.append(await send(actor, method, path, BODIES.get((method, route.path))))
    responses.append(await actor.get("/api/admin/audit?limit=100"))
    assert all(r.status_code < 500 for r in responses), [r.status_code for r in responses]
    detail = [r for r in responses if r.request.method == "GET"
              and r.request.url.path.endswith(values["user_id"])]  # fmt: skip
    assert [r.status_code for r in detail] == [200]  # the detail answered, connections and all
    for response in responses:
        leaked = [n for n in needles if len(n) >= 8 and n in response.text]
        assert leaked == [], (response.request.method, response.request.url.path)
