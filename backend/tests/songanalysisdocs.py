"""``analysis-v1`` documents of a YouTube source for the Spotify analysis stage's tests (SD-P6 stage
B, #1303): the vendored full example made to describe a 240 s song with a BPM, four sections, a
drop and an alignment to Spotify's clock. Invented values only."""

import copy
import hashlib
from typing import Any

from spotdownfake import example


def youtube_doc(
    video_id: str,
    bpm: float,
    *,
    duration: float = 240.0,
    residual: float = 0.3,
    low_start: bool = True,
    alignment: bool = True,
) -> dict[str, Any]:
    doc = copy.deepcopy(example("analysis-v1.full.json"))
    doc["provenance"]["source"]["ref"] = video_id
    doc["provenance"]["source"]["match"]["chosen"] = video_id
    doc["provenance"]["source"]["match"]["candidates"][0]["ref"] = video_id
    doc["provenance"]["audio"]["sha256"] = hashlib.sha256(video_id.encode()).hexdigest()
    for key in ("file_duration_s", "content_end_s", "content_duration_s"):
        doc["provenance"]["audio"][key] = duration
    doc["provenance"]["audio"]["content_start_s"] = 0.0
    signal = doc["signal"]
    signal |= {"duration_s": duration, "content_start_s": 0.0, "content_end_s": duration}
    signal["silence_regions"], signal["fade_out"], signal["fade_in"] = [], None, None
    n = int(duration)
    opening = 0.2 if low_start else 1.0  # the first 30 s: quiet, or louder than the rest
    values = [opening if i < 30 else 0.6 + 0.3 * ((i // 60) % 2) for i in range(n)]
    for band in ("full", "percussive", "low_band"):
        signal["energy"][band]["values"] = values
    doc["tempo"]["bpm"] = bpm
    doc["tempo"]["half_double_candidates"] = [
        {"bpm": bpm / 2, "confidence": 0.2, "method": "estimated"},
        {"bpm": bpm * 2, "confidence": 0.05, "method": "estimated"},
    ]
    step = 60.0 / bpm
    doc["grid"] = {
        "beats": {
            "times_s": [round(i * step, 3) for i in range(int(duration / step))],
            "confidence": 0.9,
            "method": "estimated",
        }
    }
    doc["structure"]["segments"] = [
        {"id": f"s{i}", "start_s": 60.0 * i, "end_s": min(60.0 * (i + 1), duration),
         "boundary_confidence": 0.8, "method": "estimated"}
        for i in range(int(duration // 60))
    ]  # fmt: skip
    doc["events"] = [
        {"kind": "drop", "start_s": 60.0, "strength": 0.9, "confidence": 0.8, "method": "estimated"}
    ]
    doc.pop("labels", None)
    if alignment:
        ref = {"kind": "spotify", "id": "0" * 32, "duration_s": duration}
        doc["alignment"] = {**doc["alignment"], "reference": ref, "offset_s": 0.0}
        doc["alignment"]["residual_s"] = residual
        doc["provenance"]["alignment"] |= {
            "reference": ref,
            "offset_s": 0.0,
            "residual_s": residual,
            "hypotheses": [{"name": "untrimmed", "offset_s": 0.0, "residual_s": residual}],
            "chosen": "untrimmed",
        }
    else:
        doc.pop("alignment")
        doc["provenance"].pop("alignment")
    return doc
