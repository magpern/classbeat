"""What the model sees of a Swedebeat mix (ADR 0016 section 3, A4; ADR 0006 section 7), and that the
Spotify source is unchanged. No database, no network.

A mix's map is the library's audio analysis (``songs``, ``events``): the model is given per song its
index, BPM, start and length, and the drops and breakdowns, nothing else. The planted-value test
puts a recognisable value in every other key a stored map could carry (a title, an artist, the mix's
name and id, MOWL's playlist id, a URL) and checks that none reaches what a provider adapter would
send in a whole design stage.
"""

import copy
import hashlib
import json
from typing import Any

import pytest

from agentfake import CONFIG, MemoryHost, analysed, brief, from_dict, good_draft
from classbeat.agent import DesignContext, design_stage, mix_input, model_input, run_stage
from classbeat.agent.design import check
from classbeat.agent.mix_input import MixData
from classbeat.agent.model_input import MusicData
from classbeat.agent.providers.fake import FakeProvider
from classbeat.agent.schemas import WorkoutDraft

PLANTED = {
    "title": "Planted Song Title 3kq",
    "artist": "Planted Artist Name 8vw",
    "name": "Planted Mix Name q9z",
    "mix_id": "planted-mix-id-77",
    "swedebeat_id": "planted-swedebeat-id-12",
    "playlist_id": 987650123,
    "url": "https://media.fake.test/planted.mp3?sig=planted-signature",
}


def mix_map(planted: bool = False) -> dict[str, Any]:
    """A map shaped like ``music.analyze_signal``'s: three songs of five minutes."""
    songs, events = [], []
    for n, bpm in enumerate((124.0, 128.0, 132.0), start=1):
        start = (n - 1) * 300.0
        song: dict[str, Any] = {
            "song": n, "start": start, "end": start + 300.0, "bpm": bpm,
            "first_downbeat": start + 0.5, "first_phrase": start + 0.5, "phrase_bars": 8,
            "grid_score": 0.9, "beat1_margin": 1.1 if n == 2 else 2.0,
        }  # fmt: skip
        events += [
            {"t": start + 60.0, "precise": start + 60.0, "kind": "drop", "strength": 0.8,
             "song": n},
            {"t": start + 180.0, "precise": start + 180.0, "kind": "breakdown", "strength": 0.6,
             "song": n},
        ]  # fmt: skip
        if planted:
            song |= {"title": PLANTED["title"], "artist": PLANTED["artist"]}
        songs.append(song)
    m: dict[str, Any] = {"version": 12, "duration": 900.0, "songs": songs, "events": events}
    if planted:
        m |= {k: PLANTED[k] for k in ("name", "mix_id", "swedebeat_id", "playlist_id", "url")}
        for e in events:
            e["label"] = PLANTED["title"]
    return m


def mix_data(m: dict[str, Any]) -> MixData:
    return MixData({"map": m, "duration": m["duration"]}, int(PLANTED["playlist_id"]))


def everything_sent(fake: FakeProvider) -> str:
    parts: list[str] = []
    for sent in fake.sent:
        spec = sent.spec
        parts += [spec.system, spec.first_message]
        parts += [json.dumps([t.name, t.description, dict(t.input_schema)]) for t in spec.tools]
        for results, reminder in sent.turns:
            parts += [r.content for r in results] + [reminder or ""]
    return "\n".join(parts)


def every_call_script() -> dict[str, Any]:
    draft = {"draft": {"$draft": "good"}}
    return {
        "drafts": {"good": good_draft()},
        "turns": [
            {
                "calls": [
                    {"name": "get_music", "input": {}},
                    {"name": "get_music", "input": {"track": 0}},
                    {"name": "get_music", "input": {"track": 1}},
                    {"name": "get_music", "input": {"track": 3}},
                    {"name": "preview_draft", "input": draft},
                ]
            },
            {"calls": [{"name": "submit_draft", "input": draft}]},
        ],
    }


async def test_no_planted_mix_value_reaches_the_provider() -> None:
    ctx = DesignContext.build(brief(), mix_data(mix_map(planted=True)))
    fake = from_dict(every_call_script())
    host = MemoryHost()
    outcome = await run_stage(host, fake, design_stage(ctx), ctx.first_message(), CONFIG)
    assert host.outcomes() == ["submitted"]
    sent = everything_sent(fake)
    assert '"bpm":128.0' in sent  # the songs' numbers do go
    for value in PLANTED.values():
        assert str(value) not in sent, value
    data = [fake.sent[0].spec.first_message.split("\n\n", 1)[1]]  # the payload, past the wording
    data += [r.content for s in fake.sent for results, _ in s.turns for r in results]
    for key in ('"title"', '"artist"', '"playlist"', '"label"', "swedebeat", "mix_id", "url"):
        assert all(key not in part for part in data), key
    assert outcome.output.crossfade_sec == 0.0


def test_the_mix_summary_is_numbers_only() -> None:
    data = mix_data(mix_map(planted=True))
    m = mix_input.music_map(data)
    summary = mix_input.music_summary(data, m)
    assert set(summary) == {"duration", "duration_sec", "crossfade_sec", "tracks",
                            "drops_and_breakdowns"}  # fmt: skip
    assert summary["crossfade_sec"] == 0.0
    assert summary["tracks"] == [
        {"index": 0, "bpm": 124.0, "from": "0:00", "sec": 300.0},
        {"index": 1, "bpm": 128.0, "from": "5:00", "sec": 300.0},
        {"index": 2, "bpm": 132.0, "from": "10:00", "sec": 300.0},
    ]
    assert set(summary["drops_and_breakdowns"][0]) == {"at", "t", "kind"}
    assert mix_input.track_count(m) == 3
    assert mix_input.track_at(m, 310.0) == 1
    detail = mix_input.track_detail(m, 1)
    assert detail == {
        "index": 1,
        "sections": [{"from": "5:00", "to": "10:00", "bpm": 128.0}],
        "drops_and_breakdowns": [
            {"at": "6:00", "t": 360.0, "kind": "drop"},
            {"at": "8:00", "t": 480.0, "kind": "breakdown"},
        ],
    }


def test_a_mix_gets_the_swedebeat_warnings_not_the_spotify_timing_warning() -> None:
    m = mix_input.music_map(mix_data(mix_map()))
    warnings = mix_input.music_warnings(m, 1000)
    assert warnings == [
        model_input.LONGER_THAN_MUSIC.format(workout="16:40", music="15:00"),
        mix_input.BEAT_ONE_ESTIMATE.format(songs="1"),
    ]
    assert model_input.TIMING_WARNING not in warnings
    corrected = copy.deepcopy(mix_map())
    corrected["songs"][1]["beat1_shift"] = 1  # a corrected beat 1 is no estimate
    assert mix_input.music_warnings(mix_input.music_map(mix_data(corrected)), 600) == []
    result = check(WorkoutDraft.model_validate(good_draft()), m)
    assert result.crossfade_sec == 0.0
    assert model_input.TIMING_WARNING not in result.warnings
    assert [i["track"] for s in result.timeline for i in s["intervals"]][:2] == [0, 0]


def test_a_spotify_map_cannot_be_read_as_a_mix() -> None:
    with pytest.raises(ValueError, match="no tracks"):
        mix_input.music_map(mix_data({"tracks": [], "songs": [], "events": [], "duration": 1.0}))


# The Spotify source, byte for byte as before A4: what the model is sent of the synthetic playlist
# (summary, first message, every track's detail, the warnings and the fitted timeline). Recorded
# from the code on main before this change; any difference fails here.
SPOTIFY_DIGEST = "6e08bf4406a9a0ee2f7cd375b5e0fcd365dede8ae8a24465ab5516cd370a04de"


def spotify_view(data: MusicData) -> str:
    ctx = DesignContext.build(brief(text="Hills"), data)
    m = ctx.map
    result = check(WorkoutDraft.model_validate(good_draft()), m)
    parts = [
        ctx.first_message(),
        json.dumps([mix_input.track_detail(m, i) for i in range(mix_input.track_count(m))]),
        json.dumps([mix_input.track_at(m, t) for t in (0.0, 235.0, 480.0, 1500.0)]),
        json.dumps(result.warnings),
        json.dumps(result.timeline),
        json.dumps(result.quality),
        str(result.crossfade_sec),
    ]
    return "\n".join(parts)


def test_the_spotify_source_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    data = analysed(monkeypatch)
    assert not isinstance(data, MixData)
    view = spotify_view(data)
    assert hashlib.sha256(view.encode()).hexdigest() == SPOTIFY_DIGEST
