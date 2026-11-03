"""What the model sees of a running class (ADR 0018 section 2), beside ``model_input``.

``model_input`` is one of the eval suite's rule sources and stays as it is, so a cycling class's
input is byte-identical; the running additions live here and reuse its allowlisted pieces:

- the brief: ``running_brief_for_model``, the cycling brief's allowlist minus the bike-only knobs
  (climbs, sprints, races, FTP test), plus ``"sport": "running"``. Nothing else of the brief, and
  never an id, an email or a connection;
- the music summary: exactly ``model_input``'s (or ``mix_input``'s) projection, unchanged;
- the fitted timeline: ``running_timeline_for_model``, per interval its start, length, zone,
  gradient, what it snapped to and the index of the track under it (no cadence, no position);
- the quality report: ``running_quality``, ``model_input.quality_for_model`` with each issue's
  sentence in running words. The library's quality sentences speak of rides ("official rides hold
  one zone ...", standing, cadence); a running class has no standing or cadence, so only the zone
  run sentence can appear, and it is replaced by ``ZONE_RUN_TEXT`` (code and numbers kept). Any
  other sentence of a cycling issue is dropped to its code (issue #465);
- the first messages: ``model_input``'s preambles with the running brief.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

from mowl.workout import mmss

from classbeat.agent import mix_input, model_input
from classbeat.agent.schemas import Brief, clean_text


def running_brief_for_model(brief: Brief) -> dict[str, Any]:
    if brief.sport != "running":
        raise ValueError("a running brief only")
    return {
        "sport": "running",
        "length_min": brief.length_min,
        "intensity": brief.intensity,
        "style": brief.style,
        "text": brief.text,
    }


def running_timeline_for_model(
    fitted: Mapping[str, Any], report: Sequence[Mapping[str, Any]], m: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Segments and intervals of the fitted running spec with start times and the track (or the
    mix's song) under each."""
    rows = iter(report)
    t = 0
    out = []
    for seg in fitted["segments"]:
        seg_start = t
        intervals = []
        for i in seg["intervals"]:
            row = next(rows, {})
            intervals.append(
                {
                    "start": mmss(t),
                    "sec": i["sec"],
                    "zone": i["zone"],
                    "gradient": int(i.get("gradient", 0)),
                    "on": str(row.get("on", "")),
                    "track": mix_input.track_at(m, t + i["sec"] / 2),
                }
            )
            t += i["sec"]
        out.append(
            {
                "name": seg["name"],
                "kind": seg["kind"],
                "start": mmss(seg_start),
                "sec": t - seg_start,
                "intervals": intervals,
            }
        )
    return out


# The library's zone run issue (past ``mowl.quality.MAX_ZONE_RUN_SEC``) in running words: its bound
# comes from official rides, so for a running class it is a hint, never a rule, and the sentence
# names no ride.
ZONE_RUN_TEXT: Final = (
    "zone {zone} for {sec} s from {start}; a long stretch at one effort: break it up unless the "
    "brief asks for a steady run"
)
# Issue codes whose library sentence is about riding (standing, cadence) and has no running words.
RIDE_ONLY_CODES: Final = frozenset(
    {"standing_too_long", "standing_rpm", "cadence_jump", "beat_off"}
)


def running_quality(
    wanted: Mapping[str, Any],
    fitted: Mapping[str, Any],
    m: Mapping[str, Any],
    report: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """``model_input.quality_for_model`` of a running class, its sentences in running words."""
    out = model_input.quality_for_model(wanted, fitted, m, report)
    run = out["longest_zone_run"]
    issues: list[dict[str, Any]] = []
    for issue in out["issues"]:
        code = issue["code"]
        if code == "zone_run_too_long":
            text = ZONE_RUN_TEXT.format(zone=run["zone"], sec=run["sec"], start=run["from"])
            issues.append({"code": code, "text": text})
        elif code in RIDE_ONLY_CODES:
            issues.append({"code": code, "text": code.replace("_", " ")})
        else:
            issues.append(issue)
    return {**out, "issues": issues}


def _with_rules(rules: str | None) -> str:
    return f"{rules}\n\n" if rules else ""


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def first_message(brief: Brief, summary: Mapping[str, Any], rules: str | None = None) -> str:
    """``model_input.first_message`` of a running class."""
    payload = {"brief": running_brief_for_model(brief), "music": summary}
    return model_input.FIRST_MESSAGE + _with_rules(rules) + _json(payload)


def revise_message(
    brief: Brief,
    summary: Mapping[str, Any],
    base: Mapping[str, Any],
    earlier_requests: Sequence[str],
    request: str,
    *,
    music_changed: bool,
    rules: str | None = None,
) -> str:
    """``model_input.revise_message`` of a running class (never songs changed: running has no
    song picking). The same parts and nothing else: no earlier note, no other model text."""
    payload: dict[str, Any] = {
        "brief": running_brief_for_model(brief),
        "music": summary,
        "base_draft": base,
        "earlier_requests": [clean_text(r) for r in earlier_requests],
        "request": clean_text(request),
    }
    head = model_input.REVISE_MESSAGE + (model_input.MUSIC_CHANGED if music_changed else "")
    return head + _with_rules(rules) + _json(payload)
