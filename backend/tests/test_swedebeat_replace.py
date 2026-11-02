"""Replace songs Swedebeat does not have, then approve the selection (ADR 0016 section 6, B3).

The catalogue fact is statuses only. A planted title, artist and catalogue id live on the match
revision (and the fake catalogue answers with a reference id) and must not reach a model's input.
The replace and the re-match run on the fakes: no network, no live account.
"""

import json
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

import pytest

from cataloguefake import PLANTED_KEY, PLANTED_REFERENCE, PLANTED_URL, CatalogueFake
from classbeat import connections, swedebeat_match
from classbeat.agent.providers.fake import FakeProvider
from classbeat.agent.schemas import Brief
from classbeat.egress import swedebeat_cloud_test_client
from classbeat.songs.revise import pick_brief
from classbeat.songs.song_list import Song, SongListContent
from classbeat.swedebeat_match import kind as match_kind
from classbeat.swedebeat_match.config import MatchConfig
from classbeat.swedebeat_match.config import current as match_config
from classbeat.swedebeat_match.replace import FACT, catalogue_fact, status_facts
from classbeat.swedebeat_match.revision import (
    MatchContent,
    Matched,
    MatchedSong,
    Wanted,
    build,
)
from ctx import make_context
from revisesongtools import change, current, picked, replace_script, revise_script
from songtools import SongEnv, song_id

PLANTED_TITLE = "Planted Catalogue Title 3kq"
PLANTED_ARTIST = "Planted Catalogue Artist 8vw"
PLANTED_WANTED = "Planted Wanted Title 3kq"
REPLACED_TITLE = "Ride Song 4 alpha"  # slot 2 of the picked list; the one the replace drops
NEW_SONG = song_id(13)

_PLANTED = (
    PLANTED_TITLE,
    PLANTED_ARTIST,
    PLANTED_WANTED,
    PLANTED_REFERENCE,
    PLANTED_URL,
    PLANTED_KEY,
)


def _row(position: int, status: str) -> MatchedSong:
    missing = status == "missing"
    return MatchedSong(
        position=position,
        status="missing" if missing else "unsure" if status == "unsure" else "found",
        wanted=Wanted(title=PLANTED_WANTED, artist=PLANTED_REFERENCE),
        swedebeat=None
        if missing
        else Matched(title=PLANTED_TITLE, artist=PLANTED_ARTIST, duration=200),
    )


def _content() -> MatchContent:
    """One found, one unsure, one missing, each carrying a planted value the fact must drop."""
    songs = [_row(1, "found"), _row(2, "unsure"), _row(3, "missing")]
    return MatchContent(
        v=1,
        song_list_hash="ab" * 32,
        songs=songs,
        found=1,
        unsure=1,
        missing=1,
        swedebeat_sec=200,
    )


def test_the_catalogue_fact_is_statuses_only() -> None:
    content = _content()
    facts = status_facts(content)
    assert facts == [
        {"position": 2, "status": "unsure"},
        {"position": 3, "status": "missing"},
    ]
    assert all(set(row) == {"position", "status"} for row in facts)
    fact = catalogue_fact(content)
    assert fact.startswith(FACT)
    assert '"status":"missing"' in fact
    assert '"status":"unsure"' in fact
    for value in _PLANTED:
        assert value not in fact
    for key in ('"title"', '"artist"', '"reference"', "catalogue id"):
        assert key not in fact
    brief = pick_brief(Brief(length_min=30, text="Keep the climb."), fact)
    for value in _PLANTED:
        assert value not in brief.text
    assert '"status":"missing"' in brief.text


def _sent(providers: list[FakeProvider]) -> str:
    parts: list[str] = []
    for provider in providers:
        for sent in provider.sent:
            parts += [sent.spec.system, sent.spec.first_message]
            for results, reminder in sent.turns:
                parts += [result.content for result in results]
                parts.append(reminder or "")
    return "\n".join(parts)


@contextmanager
def _enabled(songenv: SongEnv):
    env = songenv.spot.env
    previous_settings = env.app.state.settings
    previous = match_config()
    fresh = previous_settings.model_copy(update={"swedebeat_enabled": True})
    env.app.state.settings = fresh
    env.settings = fresh
    swedebeat_match.configure(MatchConfig(enabled=True, keyring=songenv.ring, pace_seconds=0))
    try:
        yield
    finally:
        env.app.state.settings = previous_settings
        env.settings = previous_settings
        swedebeat_match.configure(previous)


def _insert_match(
    songenv: SongEnv,
    owner: UUID,
    class_id: UUID,
    parent: UUID,
    digest: bytes,
    songs: list[MatchedSong],
) -> UUID:
    made = build(songs, song_list_id=parent, song_list_hash=digest)
    match_id = uuid4()
    songenv.sql(
        "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
        "content_hash, music, provenance, parent_id) "
        "SELECT :id, :owner, :class_id, coalesce((SELECT max(number) FROM class_revisions "
        "WHERE owner_id = :owner AND class_id = :class_id), 0) + 1, 'swedebeat_match', "
        "CAST(:content AS jsonb), :hash, CAST(:music AS jsonb), CAST(:provenance AS jsonb), "
        ":parent",
        id=match_id,
        owner=owner,
        class_id=class_id,
        content=json.dumps(made.content.model_dump(mode="json"), sort_keys=True),
        hash=made.content_hash,
        music=json.dumps(made.music.model_dump(mode="json"), sort_keys=True),
        provenance=json.dumps(made.provenance, sort_keys=True),
        parent=parent,
    )
    return match_id


def _song(position: int, song: Song, *, missing: bool) -> MatchedSong:
    if missing:
        return MatchedSong(
            position=position,
            status="missing",
            wanted=Wanted(title=PLANTED_WANTED, artist=PLANTED_REFERENCE),
            swedebeat=None,
        )
    return MatchedSong(
        position=position,
        status="found",
        wanted=Wanted(title=song.title, artist=song.artist),
        swedebeat=Matched(title=PLANTED_TITLE, artist=PLANTED_ARTIST, duration=180),
    )


async def _approve(rider: Any, class_id: UUID, match_id: UUID) -> Any:
    return await rider.browser.post(
        f"/api/classes/{class_id}/swedebeat-match", {"revision_id": str(match_id)}
    )


def _approved(songenv: SongEnv, class_id: UUID) -> UUID | None:
    return songenv.sql(
        "SELECT approved_swedebeat_match_id FROM classes WHERE id = :id", id=class_id
    ).scalar_one()


async def test_flag_off_answers_404_for_a_real_class_and_an_unknown_one(songenv: SongEnv) -> None:
    rider = await songenv.rider()
    songenv.sql(
        "INSERT INTO classes (id, owner_id, title) VALUES (:id, :owner, 'Evening')",
        id=(class_id := uuid4()),
        owner=rider.user_id,
    )
    unknown = await _approve(rider, uuid4(), uuid4())
    real = await _approve(rider, class_id, uuid4())
    assert unknown.status_code == real.status_code == 404
    assert unknown.json() == real.json() == {"detail": {"reason": "feature_off"}}
    assert _approved(songenv, class_id) is None


async def test_missing_songs_are_replaced_rematched_and_the_selection_approved(
    songenv: SongEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalogue = CatalogueFake()
    monkeypatch.setattr(
        match_kind, "catalogue_client", lambda: swedebeat_cloud_test_client(catalogue)
    )
    rider = await songenv.rider()
    await connections.save(
        songenv.factory,
        songenv.ring,
        make_context(rider.user_id),
        "swedebeat",
        {"token": "swedebeat-test-token", "device_id": "0123456789abcdef"},
        public={"email": "rider@example.com"},
    )
    with _enabled(songenv):
        class_id, base, first_list = await picked(songenv, rider)
        stored = songenv.revision(first_list)
        parsed = SongListContent.model_validate(stored.content)
        match_id = _insert_match(
            songenv,
            rider.user_id,
            class_id,
            first_list,
            bytes(stored.content_hash),
            [_song(n, song, missing=n == 3) for n, song in enumerate(parsed.songs, start=1)],
        )
        both = await change(
            rider,
            class_id,
            base,
            match_revision_id=str(match_id),
            replace=[2],
            request="use this title",
        )
        assert both.status_code == 422, both.text

        approved = await _approve(rider, class_id, match_id)
        assert approved.status_code == 200, approved.text
        assert approved.json()["revision_id"] == str(match_id)
        assert set(approved.json()) == {"revision_id", "content_hash"}
        for value in _PLANTED:
            assert value not in approved.text
        assert catalogue.calls == []
        assert _approved(songenv, class_id) == match_id

        before = len(songenv.providers)
        songenv.script = replace_script()
        started = await change(
            rider, class_id, base, replace=None, request=None, match_revision_id=str(match_id)
        )
        assert started.status_code == 201, started.text
        job_id = UUID(started.json()["id"])
        row = await songenv.run(job_id)
        assert row.state == "waiting_for_approval", (row.state, row.error_category)
        assert catalogue.calls == []
        sent = _sent(songenv.providers[before:])
        # The fact is inside the brief JSON, so its quotes are escaped. The words are not.
        assert FACT in sent
        assert "missing" in sent
        for value in (*_PLANTED, REPLACED_TITLE):
            assert value not in sent, value
        new_list = songenv.revision(row.approval_revision_id)
        assert new_list.content["songs"][2]["spotify_id"] == NEW_SONG
        assert new_list.content["songs"][2]["new"] is True
        message = songenv.sql(
            "SELECT body FROM class_messages WHERE job_id = :id AND role = 'user'", id=job_id
        ).scalar_one()
        assert message.startswith(FACT)
        for value in _PLANTED:
            assert value not in message

        digest = bytes(row.approval_hash).hex()
        answer = await rider.approve(job_id, row.approval_revision_id, digest)
        assert answer.status_code == 200
        songenv.script = revise_script()
        done = await songenv.run(job_id)
        assert (done.state, done.error_category) == ("completed", None)
        assert _approved(songenv, class_id) is None  # the new draft voided the old approval
        fresh = songenv.sql(
            "SELECT id, content FROM class_revisions WHERE class_id = :c "
            "AND kind = 'swedebeat_match' AND parent_id = :parent",
            c=class_id,
            parent=row.approval_revision_id,
        ).one()
        dumped = json.dumps(fresh.content)
        for value in _PLANTED:
            assert value not in dumped
        assert any("Ride Song 13" in unquote(call.url) for call in catalogue.calls)
        assert all(PLANTED_TITLE not in call.url for call in catalogue.calls)
        phase_two = _sent(songenv.providers[before:])
        for value in _PLANTED:
            assert value not in phase_two

        calls = len(catalogue.calls)
        again = await _approve(rider, class_id, fresh.id)
        assert again.status_code == 200, again.text
        assert _approved(songenv, class_id) == fresh.id
        repeat = await _approve(rider, class_id, fresh.id)
        assert repeat.status_code == 200, repeat.text
        assert len(catalogue.calls) == calls  # the approval calls nothing
        stale = await _approve(rider, class_id, match_id)
        assert stale.status_code == 409
        assert stale.json()["detail"]["reason"] == "stale_match"
        assert _approved(songenv, class_id) == fresh.id

        other = await songenv.rider()
        foreign = await _approve(other, class_id, fresh.id)
        assert foreign.status_code == 404
        assert foreign.json()["detail"]["reason"] == "not_found"
        assert _approved(songenv, class_id) == fresh.id

        base_now = current(songenv, class_id)
        for _ in range(2):
            extra = await change(
                rider,
                class_id,
                base_now,
                replace=None,
                request=None,
                match_revision_id=str(fresh.id),
            )
            assert extra.status_code == 201, extra.text
            cancelled = await rider.browser.post(f"/api/jobs/{extra.json()['id']}/cancel")
            assert cancelled.status_code == 200, cancelled.text
        limited = await change(
            rider, class_id, base_now, replace=None, request=None, match_revision_id=str(fresh.id)
        )
        assert limited.status_code == 409, limited.text
        assert limited.json()["detail"]["reason"] == "swedebeat_rounds"
        assert limited.json()["detail"]["limit"] == 3
        assert limited.json()["detail"]["count"] == 3

        songenv.spot.env.app.state.settings = songenv.spot.env.app.state.settings.model_copy(
            update={"swedebeat_enabled": False}
        )
        songenv.spot.env.settings = songenv.spot.env.app.state.settings
        gated = await _approve(rider, class_id, fresh.id)
        assert gated.status_code == 404
        assert gated.json()["detail"]["reason"] == "feature_off"
        assert _approved(songenv, class_id) == fresh.id
