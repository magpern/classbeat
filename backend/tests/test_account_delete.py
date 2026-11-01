"""Account deletion (ADR 0014 section 3; #337): a typed confirmation, open jobs ended and their
credits settled first, then every table that holds the user's data is empty of it. The scan reads
every column of every table from the catalog, so a table added later is checked without a list."""

from typing import Any

import pytest

from accounttools import Person, hits, person, references_with_rows
from classbeat import quota
from classbeat.account import erase
from classbeat.account.coverage import RETAINED
from ctx import make_context
from songtools import SongEnv

pytestmark = pytest.mark.usefixtures("songenv")

URL = "/api/account/delete"
# Tables with a user reference that the fixtures cannot fill with one honest row: none today. A
# table lands here only with a reason, and the plant test fails for any other empty one.
UNPLANTED: dict[tuple[str, str], str] = {
    ("spotify_oauth_flows", "owner_id"): "an unfinished connect flow: deleted when used",
}


async def erase_account(who: Person, typed: str | None = None) -> Any:
    body = {"confirm_email": who.email if typed is None else typed}
    return await who.rider.browser.post(URL, body)


async def test_every_table_with_a_user_reference_has_a_row_before_and_none_after(
    songenv: SongEnv,
) -> None:
    mine, other = await person(songenv), await person(songenv)
    before = references_with_rows(mine)
    assert {key for key, count in before.items() if count == 0} <= set(UNPLANTED), before
    assert hits(mine), "the scan finds the planted user"
    other_hits, other_before = hits(other), references_with_rows(other)

    answer = await erase_account(mine)
    assert answer.status_code == 204, answer.text
    assert "Max-Age=0" in answer.headers["set-cookie"]  # the session cookie is cleared

    # Nowhere in the schema is the user's id, address or IP left, as a uuid or written as text in a
    # text, varchar, jsonb or inet column, except the bare id an operator's delete audits.
    left = hits(mine)
    assert left == {"admin_audit.target_id": 1}, left  # the one retained bare id (RETAINED)
    assert ("admin_audit", "target_id") in RETAINED
    # The links that only named them stayed, unlinked.
    assert songenv.sql("SELECT count(*) FROM invites WHERE used_by IS NULL").scalar_one() == 1
    assert (
        songenv.sql(
            "SELECT count(*) FROM admin_audit WHERE action = 'users.get' AND target_user_id IS NULL"
        ).scalar_one()
        == 1
    )
    assert (
        songenv.sql(
            "SELECT count(*) FROM entitlements e JOIN users u ON u.id = e.owner_id "
            "WHERE e.granted_by IS NULL AND u.email = :e",
            e=mine.third_email,
        ).scalar_one()
        == 1
    )  # a grant I made to someone else stays, without me
    # The other user lost nothing.
    assert hits(other) == other_hits
    assert references_with_rows(other) == other_before


async def test_the_scan_catches_a_row_the_deletion_would_miss(songenv: SongEnv) -> None:
    """The scan can fail: a table with no foreign key that names the user is reported."""
    mine = await person(songenv)
    songenv.sql("CREATE TABLE gdpr_probe (id uuid, note text)")
    try:
        songenv.sql("INSERT INTO gdpr_probe VALUES (:u, :email)", u=mine.user_id, email=mine.email)
        assert (await erase_account(mine)).status_code == 204
        assert hits(mine) == {
            "gdpr_probe.id": 1,
            "gdpr_probe.note": 1,
            "admin_audit.target_id": 1,  # the retained bare id, as in the main test
        }
    finally:
        songenv.sql("DROP TABLE gdpr_probe")


async def test_the_typed_address_must_be_the_accounts_and_nothing_changes_otherwise(
    songenv: SongEnv,
) -> None:
    mine = await person(songenv)
    other = await person(songenv)
    before = references_with_rows(mine)
    for typed in ("", "nobody@example.com", other.email, mine.email + "x", mine.email[1:]):
        answer = await erase_account(mine, typed)
        assert (answer.status_code, answer.json()["detail"]["reason"]) == (
            400,
            "confirmation_mismatch",
        ), typed
    assert (await erase_account(mine, "  " + mine.email.upper() + " ")).status_code == 204
    assert before["classes", "owner_id"] == 1


async def test_deleting_needs_a_session_recent_authentication_and_the_step_up(
    songenv: SongEnv,
) -> None:
    mine = await person(songenv)
    anonymous = await songenv.spot.env.client.post(URL, json={"confirm_email": mine.email})
    assert anonymous.status_code in (401, 403)
    songenv.sql("UPDATE sessions SET stepup_at = NULL")
    refused = await erase_account(mine)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (403, "stepup_required")
    songenv.sql("UPDATE sessions SET stepup_at = now(), reauth_at = now() - interval '11 minutes'")
    refused = await erase_account(mine)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (403, "reauth_required")
    assert songenv.sql("SELECT count(*) FROM users WHERE id = :u", u=mine.user_id).scalar_one() == 1
    assert mine.count("classes") == 1


async def test_open_jobs_end_and_their_credits_settle_before_the_account_goes(
    songenv: SongEnv,
) -> None:
    mine = await person(songenv)
    job_id = await songenv.created(mine.rider, title="Second class")
    state = songenv.row(job_id)
    assert (state.state, state.credit_state) == ("queued", "reserved")
    held = songenv.sql(
        "SELECT state FROM credit_ledger WHERE owner_id = :o AND job_id = :j",
        o=mine.user_id,
        j=job_id,
    ).scalar_one()
    assert held == "reserved"

    ended = await erase.end_jobs(
        songenv.factory, make_context(mine.user_id), ledger_for(songenv), wait=0
    )
    assert ended.cancelled >= 1
    assert ended.still_running == 0
    after = songenv.row(job_id)
    assert (after.state, after.credit_state) == ("cancelled", "released")
    assert (
        songenv.sql(
            "SELECT state FROM credit_ledger WHERE owner_id = :o AND job_id = :j",
            o=mine.user_id,
            j=job_id,
        ).scalar_one()
        == "released"
    )
    assert (
        songenv.sql(
            "SELECT count(*) FROM jobs WHERE owner_id = :o AND state IN ('queued', 'running', "
            "'waiting_for_approval')",
            o=mine.user_id,
        ).scalar_one()
        == 0
    )
    assert (await erase_account(mine)).status_code == 204


async def test_the_delete_request_ends_jobs_and_settles_credits_before_the_row_goes(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine = await person(songenv)
    job_id = await songenv.created(mine.rider, title="Second class")
    seen: list[Any] = []
    original = erase.delete_row

    async def spy(factory: Any, ctx: Any) -> bool:
        seen.append(songenv.row(job_id))  # what the database holds when the row is about to go
        seen.append(
            songenv.sql("SELECT state FROM credit_ledger WHERE job_id = :j", j=job_id).scalar_one()
        )
        return await original(factory, ctx)

    monkeypatch.setattr(erase, "delete_row", spy)
    assert (await erase_account(mine)).status_code == 204
    row, ledger_state = seen
    assert (row.state, row.credit_state, ledger_state) == ("cancelled", "released", "released")


async def test_a_running_job_is_asked_to_stop_and_the_account_goes_anyway(
    songenv: SongEnv,
) -> None:
    mine = await person(songenv)
    job_id = await songenv.created(mine.rider, title="Running class")
    songenv.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
    claimed = await songenv.claim()
    assert claimed is not None
    assert songenv.row(job_id).state == "running"

    ended = await erase.end_jobs(
        songenv.factory, make_context(mine.user_id), ledger_for(songenv), wait=0.3
    )
    row = songenv.row(job_id)
    assert row.cancel_requested_at is not None
    assert (ended.still_running, row.state) == (1, "running")
    assert (
        row.credit_state == "reserved"
    )  # settles when the worker ends it, or goes with the ledger

    assert (await erase_account(mine)).status_code == 204
    assert mine.count("jobs") == 0
    assert mine.count("credit_ledger") == 0
    assert songenv.sql("SELECT count(*) FROM jobs WHERE id = :id", id=job_id).scalar_one() == 0


async def test_a_second_delete_finds_nothing(songenv: SongEnv) -> None:
    mine = await person(songenv)
    assert (await erase_account(mine)).status_code == 204
    again = await erase_account(mine)
    assert again.status_code in (401, 403, 404)  # no session, no CSRF token, no account


async def test_no_request_leaves_for_a_provider(songenv: SongEnv) -> None:
    """Deleting asks nobody: the fakes see no new request (playlists in Spotify stay)."""
    mine = await person(songenv)
    before = songenv.counts()
    assert (await erase_account(mine)).status_code == 204
    assert songenv.counts() == before


def ledger_for(songenv: SongEnv) -> Any:
    return quota.MeteredLedger(quota.Bounds.from_settings(songenv.spot.env.settings))
