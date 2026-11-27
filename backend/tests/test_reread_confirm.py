"""The re-read of the confirm screen (#1538): the closed ``no_music: true`` field and the picked
``music_sources`` of ``POST /api/classes/{id}/reread``, against Postgres as ``classbeat_app``, the
fake MOWL and ``FakeProvider``. "No music" is never read from words; the playlists are checked
like the request's own, added to its attached ones, and need the Spotify connection and the
composer; each refusal leaves no job, no message and no credit. Never the network, a real
account or a model."""

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import composer, interpret_admit, music_kind
from interprettools import InterpretEnv, interpreter
from revisetools import ReviseEnv

pytestmark = pytest.mark.usefixtures("jenv")

A, B = "0123456789abcdefABCDEF", "ABCDEF0123456789abcdef"
SOURCES = [{"kind": "spotify_playlist", "id": A}]
OTHER = [{"kind": "spotify_playlist", "id": B}]
WHICH = {
    "topic": "music",
    "code": "which_music",
    "options": ["my_playlists", "no_music"],
    "no_music_index": 1,
}
MY_PLAYLISTS = {"question_index": 0, "option_index": 0}
NO_MUSIC = {"question_index": 0, "option_index": 1}
INVALID = (422, "invalid_input")


@pytest.fixture(autouse=True)
def composer_on(renv: ReviseEnv) -> Iterator[None]:
    composer.configure(composer.ComposerGate(enabled=True))
    music_kind.configure(music_kind.SoundlessGate(fake_covers=True))
    renv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")
    renv.sql(
        "INSERT INTO app_settings (key, value) VALUES ('composer_flow', CAST(:v AS jsonb))",
        v=json.dumps("on"),
    )
    try:
        yield
    finally:
        composer.configure(None)
        music_kind.configure(None)
        renv.sql("DELETE FROM app_settings WHERE key = 'composer_flow'")


@pytest.fixture
def picking(monkeypatch: pytest.MonkeyPatch) -> None:
    async def admits(*_args: Any) -> bool:
        return True

    monkeypatch.setattr(interpret_admit, "song_picking", admits)


async def pick_class(
    ienv: InterpretEnv, *, composing: bool = True, **more: Any
) -> tuple[UUID, UUID]:
    """A class read as a song pick with no hint (the closed question), Spotify connected."""
    if not composing:
        composer.configure(composer.ComposerGate(enabled=False))
    spotify = str(await ienv.connect_spotify())
    return await ienv.interpreted({}, spotify=False, spotify_connection_id=spotify, **more)


def path(class_id: UUID) -> str:
    return f"/api/classes/{class_id}/reread"


def messages(ienv: InterpretEnv, class_id: UUID) -> list[str]:
    rows = ienv.sql("SELECT body FROM class_messages WHERE class_id = :c ORDER BY seq", c=class_id)
    return [r.body for r in rows.all()]


async def refused(
    ienv: InterpretEnv, class_id: UUID, body: dict[str, Any], status: int, reason: str
) -> None:
    """The re-read is refused with that status and reason, and nothing is written."""
    before = (ienv.counts(), messages(ienv, class_id))
    answer = await ienv.renv.browser.post(path(class_id), body)
    assert (answer.status_code, answer.json()["detail"]["reason"]) == (status, reason)
    assert (ienv.counts(), messages(ienv, class_id)) == before


async def made(ienv: InterpretEnv, class_id: UUID, body: dict[str, Any]) -> UUID:
    answer = await ienv.renv.browser.post(path(class_id), body)
    assert answer.status_code == 201, answer.text
    return UUID(answer.json()["job_id"])


# No music


async def test_no_music_is_a_closed_field_that_makes_the_reread_soundless(
    ienv: InterpretEnv,
) -> None:
    class_id, first = await ienv.interpreted({}, spotify=False)
    classes, jobs, credits = ienv.counts()
    job = await made(ienv, class_id, {"interpret_job_id": str(first), "no_music": True})
    assert ienv.counts() == (classes, jobs + 1, credits)  # one more job, no credit
    given = ienv.stored_input(job)
    assert given["music_override"] == {"source": "none"}
    assert messages(ienv, class_id)[-1] == "No music for this class."
    ienv.models(interpreter({"length_min": {"value": 45, "origin": "explicit"}}))
    await ienv.run("interpret_request")
    assert ienv.interpretation(class_id)["music"] == {"source": "none"}  # stored as soundless


async def test_no_music_is_never_read_from_the_words(ienv: InterpretEnv) -> None:
    class_id, first = await ienv.interpreted({}, spotify=False)
    job = await made(
        ienv, class_id, {"interpret_job_id": str(first), "note": "no music, please. No music."}
    )
    assert ienv.stored_input(job).get("music_override") is None


@pytest.mark.parametrize(
    "extra",
    [
        {"no_music": False},
        {"no_music": "true"},
        {"no_music": 1},
        {"no_music": None, "music_override": {"source": "none"}},
        {"no_music": True, "music_sources": SOURCES},  # "No music" and playlists contradict
    ],
    ids=["false", "string", "number", "override", "with playlists"],
)
async def test_a_no_music_field_that_is_not_the_closed_true_is_refused(
    ienv: InterpretEnv, extra: dict[str, Any]
) -> None:
    class_id, first = await ienv.interpreted({}, spotify=False)
    await refused(ienv, class_id, {"interpret_job_id": str(first), **extra}, *INVALID)


async def test_no_music_drops_the_playlists_the_request_attached(ienv: InterpretEnv) -> None:
    class_id, first = await pick_class(ienv, music_sources=SOURCES)
    job = await made(ienv, class_id, {"interpret_job_id": str(first), "no_music": True})
    given = ienv.stored_input(job)
    assert given["music_override"] == {"source": "none"}
    assert given.get("music_sources") is None


async def test_no_music_needs_no_spotify_but_a_song_pick_does(ienv: InterpretEnv) -> None:
    class_id, first = await pick_class(ienv, music_override={"source": "pick"})
    ienv.sql("DELETE FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=ienv.owner)
    again = {"interpret_job_id": str(first), "note": "Make it shorter"}
    await refused(ienv, class_id, again, 409, "not_connected")  # the pick would need Spotify
    job = await made(ienv, class_id, {"interpret_job_id": str(first), "no_music": True})
    assert ienv.stored_input(job)["music_override"] == {"source": "none"}


# Playlists


async def test_the_picked_playlists_are_checked_and_added_to_the_attached_ones(
    ienv: InterpretEnv,
) -> None:
    class_id, first = await pick_class(ienv, music_sources=SOURCES)
    picked = SOURCES + OTHER  # the first is attached already: each once
    job = await made(ienv, class_id, {"interpret_job_id": str(first), "music_sources": picked})
    given = ienv.stored_input(job)
    assert given["music_sources"] == SOURCES + OTHER
    assert given["spotify_connection_id"] == str(ienv.spotify_id)
    assert messages(ienv, class_id)[-1] == "Use the playlists I picked."


@pytest.mark.parametrize(
    "sources",
    [
        [{"kind": "spotify_playlist", "id": "short"}],  # not a Spotify id
        [{"kind": "mix", "id": A}],  # only Spotify playlists
        [{"kind": "spotify_playlist", "id": A, "extra": 1}],
        [{"kind": "spotify_playlist", "id": c * 22} for c in "abcdef"],  # six
        SOURCES + SOURCES,  # one twice
        [],  # none is not a pick
        "playlist",
    ],
    ids=["id", "kind", "extra", "six", "twice", "empty", "string"],
)
async def test_playlists_that_do_not_check_are_refused(ienv: InterpretEnv, sources: Any) -> None:
    class_id, first = await pick_class(ienv)
    await refused(
        ienv, class_id, {"interpret_job_id": str(first), "music_sources": sources}, *INVALID
    )


async def test_more_than_five_with_the_attached_ones_is_refused(ienv: InterpretEnv) -> None:
    attached = [{"kind": "spotify_playlist", "id": c * 22} for c in "abcd"]
    class_id, first = await pick_class(ienv, music_sources=attached)
    body = {"interpret_job_id": str(first), "music_sources": SOURCES + OTHER}
    await refused(ienv, class_id, body, *INVALID)


async def test_playlists_replace_the_forms_no_music(ienv: InterpretEnv) -> None:
    class_id, first = await ienv.interpreted({}, spotify=False)
    ienv.sql(
        "UPDATE jobs SET input = input || CAST(:f AS jsonb) WHERE id = :j",
        j=first,
        f=json.dumps({"music_override": {"source": "none"}}),
    )
    spotify = str(await ienv.connect_spotify())
    ienv.sql(
        "UPDATE jobs SET input = input || CAST(:f AS jsonb) WHERE id = :j",
        j=first,
        f=json.dumps({"spotify_connection_id": spotify}),
    )
    job = await made(ienv, class_id, {"interpret_job_id": str(first), "music_sources": SOURCES})
    given = ienv.stored_input(job)
    assert given.get("music_override") is None
    assert given["music_sources"] == SOURCES


async def test_playlists_need_the_spotify_connection(ienv: InterpretEnv) -> None:
    class_id, first = await ienv.interpreted({}, spotify=False)
    body = {"interpret_job_id": str(first), "music_sources": SOURCES}
    await refused(ienv, class_id, body, 409, "not_connected")


async def test_playlists_need_the_composer(ienv: InterpretEnv) -> None:
    class_id, first = await pick_class(ienv, composing=False)
    body = {"interpret_job_id": str(first), "music_sources": SOURCES}
    await refused(ienv, class_id, body, 409, "feature_off")


@pytest.mark.usefixtures("picking")
async def test_the_closed_playlist_answer_needs_the_playlists_it_names(ienv: InterpretEnv) -> None:
    class_id, first = await pick_class(ienv)
    assert ienv.interpretation(class_id)["questions"] == [WHICH]
    alone = {"interpret_job_id": str(first), "answers": [MY_PLAYLISTS]}
    await refused(ienv, class_id, alone, 422, "invalid_answer")  # it loops back to the question
    job = await made(ienv, class_id, alone | {"music_sources": SOURCES})
    assert ienv.stored_input(job)["music_sources"] == SOURCES
    said = "What music should the class have? Answer: Use one of my playlists"
    assert messages(ienv, class_id)[-1] == said  # not said twice


@pytest.mark.usefixtures("picking")
async def test_the_marked_no_music_answer_and_playlists_contradict(ienv: InterpretEnv) -> None:
    class_id, first = await pick_class(ienv)
    body = {"interpret_job_id": str(first), "answers": [NO_MUSIC], "music_sources": SOURCES}
    await refused(ienv, class_id, body, 422, "invalid_answer")
    job = await made(ienv, class_id, {"interpret_job_id": str(first), "answers": [NO_MUSIC]})
    assert ienv.stored_input(job)["music_override"] == {"source": "none"}
    assert messages(ienv, class_id)[-1].endswith("Answer: No music")  # the marked answer, once
