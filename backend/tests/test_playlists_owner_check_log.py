"""The owner check's refusal log (#1153): one warning per refused check, naming which condition
failed as flags and value kinds only, never a playlist id, a name, the owner or the account. The
check itself (``common.owned``) is unchanged: these tests also pin that it still refuses.
Fakes only (``spotifycore``): no network, no real account.
"""

import json
import logging
from typing import Any

import pytest

from classbeat.log import JsonFormatter
from classbeat.playlists import common
from classbeat.playlists.owner_check_policy import owner_form, refusal, value_kind
from playlisttools import MANY, PlayEnv

PID = "A" * 22
ACCOUNT = "Rider.One"
NAME = "classbeat working copy"
LOGGER = "classbeat.playlists.common"


def playlist(**changes: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "id": PID,
        "name": NAME,
        "description": "classbeat ref 0123456789ab",
        "public": False,
        "collaborative": False,
        "snapshot_id": "snap-secretish",
        "owner_id": ACCOUNT,
        "total": 3,
    }
    for key, value in changes.items():
        if value is ...:
            found.pop(key)
        else:
            found[key] = value
    return found


# --- the pure description ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "kind"),
    [(True, "true"), (False, "false"), (None, "null"), (..., "missing"), (0, "other"),
     ("false", "other")],
)  # fmt: skip
def test_value_kind_names_each_kind(value: Any, kind: str) -> None:
    assert value_kind(playlist(public=value), "public") == kind


@pytest.mark.parametrize(
    ("owner", "account", "form"),
    [(ACCOUNT, ACCOUNT, "same"), ("rider.one", ACCOUNT, "case"), ("someone", ACCOUNT, "other"),
     (None, ACCOUNT, "null"), (7, ACCOUNT, "not_string"), (ACCOUNT, None, "no_account")],
)  # fmt: skip
def test_owner_form_compares_without_values(owner: object, account: str | None, form: str) -> None:
    assert owner_form(owner, account) == form


@pytest.mark.parametrize(
    ("changes", "account", "expected"),
    [
        ({"public": None}, ACCOUNT, {"public_kind": "null", "owner_ok": True}),
        ({"public": "yes"}, ACCOUNT, {"public_kind": "other"}),
        ({"public": ...}, ACCOUNT, {"public_kind": "missing"}),
        ({"collaborative": None}, ACCOUNT, {"collaborative_kind": "null"}),
        ({"collaborative": True}, ACCOUNT, {"collaborative_kind": "true"}),
        ({"owner_id": "rider.one"}, ACCOUNT, {"owner_ok": False, "owner_form": "case"}),
        ({"owner_id": "someone"}, ACCOUNT, {"owner_ok": False, "owner_form": "other"}),
        ({"owner_id": ...}, ACCOUNT, {"owner_ok": False, "owner_form": "missing"}),
        ({"id": "B" * 22}, ACCOUNT, {"id_ok": False, "owner_ok": True}),
        ({}, None, {"account_ok": False, "owner_ok": False, "owner_form": "no_account"}),
    ],
)
def test_refusal_names_the_failed_condition(
    changes: dict[str, Any], account: str | None, expected: dict[str, object]
) -> None:
    found = playlist(**changes)
    assert not common.owned(found, PID, account)  # the rule still refuses each of these
    fields = refusal(found, PID, account, "pool")
    assert (fields["purpose"], fields["answer"]) == ("pool", "mapping")
    assert {key: fields[key] for key in expected} == expected
    assert set(fields) == {
        "purpose", "answer", "account_ok", "id_ok", "owner_ok", "owner_form", "public_kind",
        "collaborative_kind",
    }  # fmt: skip


def test_refusal_of_an_answer_that_is_not_a_mapping() -> None:
    assert refusal(["x"], PID, ACCOUNT, "final") == {
        "purpose": "final",
        "answer": "not_mapping",
        "account_ok": True,
    }


# --- the log line ------------------------------------------------------------------------------


def logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    formatter = JsonFormatter()
    return [
        json.loads(formatter.format(r))
        for r in caplog.records
        if r.name == LOGGER and r.getMessage() == "spotify owner check refused"
    ]


def test_an_owned_playlist_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert common.checked_owner(playlist(), PID, ACCOUNT, "pool", "write")
    assert logged(caplog) == []


def test_a_refusal_logs_one_warning_with_flags_and_kinds_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER)
    found = playlist(public=None, owner_id="someone-else")
    assert not common.checked_owner(found, PID, ACCOUNT, "final", "reconcile")
    [line] = logged(caplog)
    assert line["level"] == "WARNING"
    # The JSON formatter masks fields named ``public``; these reach the line unmasked.
    assert {k: line[k] for k in ("purpose", "step", "owner_ok", "owner_form", "public_kind")} == {
        "purpose": "final",
        "step": "reconcile",
        "owner_ok": False,
        "owner_form": "other",
        "public_kind": "null",
    }
    text = json.dumps(line)
    for value in (PID, ACCOUNT, ACCOUNT.lower(), "someone-else", NAME, "snap-secretish",
                  "0123456789ab"):  # fmt: skip
        assert value not in text


# --- through a job -----------------------------------------------------------------------------


async def test_a_pool_replace_refused_for_public_null_logs_why_and_writes_nothing(
    penv: PlayEnv, caplog: pytest.LogCaptureFixture
) -> None:
    owner = await penv.user()
    first = penv.job(owner)
    assert (await penv.run(first)).state == "completed"
    playlist_id = penv.pool(owner).playlist_id
    penv.fake.seen.clear()
    # Spotify answering ``public: null`` (the fake's field is typed bool; the answer is not).
    penv.fake.playlists[playlist_id].public = None  # pyright: ignore[reportAttributeAccessIssue]
    caplog.set_level(logging.WARNING, logger=LOGGER)
    row = await penv.run(penv.job(owner, tracks=MANY))
    assert (row.state, row.error_category) == ("failed", "pool_unusable")
    assert not [s for s in penv.writes() if playlist_id in s.path]
    [line] = logged(caplog)
    assert (line["purpose"], line["step"], line["public_kind"]) == ("pool", "write", "null")
    assert (line["id_ok"], line["owner_ok"], line["owner_form"]) == (True, True, "same")
    assert line["collaborative_kind"] == "false"
    assert playlist_id not in json.dumps(line)
