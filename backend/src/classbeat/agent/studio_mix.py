"""The studio's music for the model and its composition (ADR 0036 sections 4.2 to 4.4, SR C5).
Pure: no I/O, no reference.

- ``StudioTrack``: one approved track as the stage holds it: the list's Spotify title and artist,
  its analysis (``mowl.track``'s map) and its stored timing class (``None`` for a local analysis);
  ``facts`` adds the cut candidates stored with a spotdown map (#1328, trust inherited again).
  **No catalogue reference**: the stage names tracks by index, and only the backend maps an index
  back to a reference (``swedebeat_studio.design``).
- ``music_summary`` / ``track_detail``: per track its index, title, artist, BPM, length, bar
  length and first downbeat, the drops and breakdowns in the track's own seconds, its timing class
  when spotdown analysed it and its edge cut points (``cut_points_input``); a track's detail adds
  its sections with their energy and every cut point but the downbeats. Numbers come from the
  analysis only.
- ``compose``: the model's plan (indexes, cuts, crossfades) checked by ``mix_plan_policy`` against
  the analyses (``ToolError('invalid_mix')`` with the named reason), then ``mowl.compose.
  compose_map``: the map ``fit_to_music`` reads, with ``crossfade`` 0 because the crossfades are
  already in it (as a mix's map). The plan comes back snapped, by index, with the soft checks
  of ``mix_hint_policy`` (``hints``: piece positions only), which the answer adds to its warnings.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from mowl import compose as mix
from mowl.workout import mmss

from classbeat.agent import cut_points_input, mix_input, model_input
from classbeat.agent.model_input import EVENT_KINDS, view_text
from classbeat.agent.stage import ToolError
from classbeat.agent.studio_schemas import MixInput
from classbeat.spotdown.cut_candidates_model import MAP_KEY
from classbeat.spotdown.timing_inputs_model import TimingClass
from classbeat.studio import mix_hint_policy
from classbeat.studio.mix_facts_model import stored_candidates
from classbeat.studio.mix_plan_policy import (
    CS,
    MixPlanRefused,
    TrackFacts,
    checked,
    cs,
    total_sec,
)

BEAT_ONE_HEAD: Final = mix_input.BEAT_ONE_ESTIMATE.split("{", 1)[0]
COMPOSE_FAILED: Final = "compose_failed"  # named, though the policy leaves the library no rule
KEY: Final = "t{}"  # the stage's own stand-in for a reference: the index, never the catalogue's


@dataclass(frozen=True)
class StudioTrack:
    title: str
    artist: str
    music_map: Mapping[str, Any]
    timing_class: TimingClass | None = None  # the stored decision's; ``None``: a local analysis

    def facts(self) -> TrackFacts:
        (song,) = self.music_map["songs"]
        return TrackFacts(
            float(self.music_map["duration"]),
            float(song["bpm"]),
            float(song["first_downbeat"]),
            self.timing_class,
            stored_candidates(self.music_map.get(MAP_KEY), self.timing_class),
        )


@dataclass(frozen=True)
class Composed:
    plan: dict[str, Any]  # {"tracks": [{"track", "start_sec", "end_sec"}], "crossfades"}
    map: dict[str, Any]
    duration_sec: float
    hints: tuple[str, ...] = ()  # mix_hint_policy's texts: codes and piece positions only


def _events(m: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"at": mmss(e["t"]), "t": round(float(e["t"]), 1), "kind": str(e["kind"])}
        for e in m.get("events") or []
        if e.get("kind") in EVENT_KINDS
    ]


def _track(index: int, track: StudioTrack) -> dict[str, Any]:
    facts = track.facts()
    timing = {} if facts.timing_class is None else {"timing": facts.timing_class}
    points = cut_points_input.edge_points(facts)
    return {
        "index": index,
        "title": view_text(track.title),
        "artist": view_text(track.artist),
        "bpm": round(facts.bpm, 1),
        "length_sec": round(facts.length, 2),
        "bar_sec": round(240.0 / facts.bpm, 3),
        "first_downbeat_sec": round(facts.first_downbeat, 2),
        "drops_and_breakdowns": _events(track.music_map),
        **timing,
        **({"cut_points": points} if points else {}),
    }


def music_summary(tracks: Sequence[StudioTrack]) -> dict[str, Any]:
    whole = sum(t.facts().length for t in tracks)
    summary = {
        "tracks": [_track(n, t) for n, t in enumerate(tracks)],
        "all_tracks_whole_sec": round(whole, 1),
    }
    return model_input._truncated(summary)  # pyright: ignore[reportPrivateUsage]


def track_detail(tracks: Sequence[StudioTrack], index: int) -> dict[str, Any]:
    """Track ``index`` (already checked) with its sections and their energy."""
    track = tracks[index]
    sections = [
        {"from_sec": round(float(s["start"]), 2), "to_sec": round(float(s["end"]), 2),
         "energy": s.get("energy")}
        for s in track.music_map.get("sections") or []
    ]  # fmt: skip
    points = cut_points_input.all_points(track.facts())
    return {
        **_track(index, track),
        "sections": sections,
        **({"cut_points": points} if points else {}),
    }


def _whole(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def compose(tracks: Sequence[StudioTrack], given: MixInput) -> Composed:
    """The plan checked and composed; ``ToolError('invalid_mix', [reason: detail])``."""
    facts = {KEY.format(n): t.facts() for n, t in enumerate(tracks)}
    spec = {
        "tracks": [
            {"reference": KEY.format(t.track), "start_sec": t.start_sec, "end_sec": t.end_sec}
            if t.track < len(tracks)
            else {"reference": "", "start_sec": t.start_sec, "end_sec": t.end_sec}
            for t in given.tracks
        ],
        "crossfades": [_whole(c) for c in given.crossfades],
    }
    try:
        good = checked(spec, list(facts), facts)
    except MixPlanRefused as refused:
        raise ToolError("invalid_mix", [f"{refused.reason}: {refused}"]) from None
    order = [int(t["reference"][1:]) for t in good["tracks"]]
    try:
        composed = mix.compose_map([dict(tracks[n].music_map) for n in order], good)
    except (ValueError, KeyError, TypeError):
        failed = f"{COMPOSE_FAILED}: the mix could not be composed"
        raise ToolError("invalid_mix", [failed]) from None
    composed["crossfade"] = 0.0  # the crossfades are in the composed map already
    plan = {
        "tracks": [
            {"track": n, "start_sec": t["start_sec"], "end_sec": t["end_sec"]}
            for n, t in zip(order, good["tracks"], strict=True)
        ],
        "crossfades": good["crossfades"],
    }
    windows = [(cs(t["end_sec"]) - cs(t["start_sec"])) / CS for t in good["tracks"]]  # exact cs
    found = mix_hint_policy.hints(good, {k: f.length for k, f in facts.items()})
    hints = tuple(mix_hint_policy.texts(found))
    return Composed(plan, composed, total_sec(windows, good["crossfades"]), hints)


def by_list_index(composed: Composed, timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fitted timeline with each interval's ``track`` as the list's index, not its position
    in the mix (the composed map numbers its songs in the order they play)."""
    order = [t["track"] for t in composed.plan["tracks"]]
    out: list[dict[str, Any]] = []
    for segment in timeline:
        intervals = [
            {**i, "track": order[i["track"]]}
            if isinstance(i.get("track"), int) and 0 <= i["track"] < len(order)
            else i
            for i in segment.get("intervals", [])
        ]
        out.append({**segment, "intervals": intervals})
    return out


def warnings_by_list_index(composed: Composed, warnings: Sequence[str]) -> list[str]:
    """``warnings`` with the beat-one estimate named by the list's indexes, then the mix's soft
    checks (``Composed.hints``)."""
    order = [t["track"] for t in composed.plan["tracks"]]
    unsure = sorted({order[n] for n in mix_input.unsure_beat_one(composed.map) if n < len(order)})
    kept = [w for w in warnings if not w.startswith(BEAT_ONE_HEAD)]
    if unsure:
        kept.append(mix_input.BEAT_ONE_ESTIMATE.format(songs=", ".join(str(n) for n in unsure)))
    return [*kept, *composed.hints]


def plan_for_model(composed: Composed) -> dict[str, Any]:
    """The plan as the backend snapped it, with each window's played length and the total."""
    return {
        "tracks": [
            {**t, "played_sec": round(t["end_sec"] - t["start_sec"], 2)}
            for t in composed.plan["tracks"]
        ],
        "crossfades": composed.plan["crossfades"],
        "total_sec": composed.duration_sec,
        "total": mmss(composed.duration_sec),
    }
