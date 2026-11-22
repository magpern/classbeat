"""#1391 (ADR 0042 section 5): ``applied_form.unconfirmed``, the pins to listen to again, read from
the stored spec and the overlay's pins. An unconfirmed overlay pin whose analysed file is not the
one it was set against is one; a pin set against the recorded file is not; a spec or record that
does not read is one (fail closed). No I/O: the draft is a stand-in with the fields it reads."""

from types import SimpleNamespace
from typing import Any

import pytest

from classbeat.design.edit_overlay_model import OverlayPin, of_records
from classbeat.swedebeat_studio import applied_form

SHA, OTHER = "a" * 64, "b" * 64
SPEC = {
    "tracks": [
        {
            "reference": "r0",
            "start_sec": 0.0,
            "end_sec": 200.0,
            "end_pin": {"requested_sec": 200.0, "mode": "exact", "confirmed": False},
        },
        {"reference": "r1", "start_sec": 0.0, "end_sec": 240.0},
    ],
    "crossfades": [8.0],
}


def pin(set_against: str) -> OverlayPin:
    return OverlayPin.model_validate(
        {
            "type": "pin",
            "reference": "r0",
            "occurrence": 0,
            "edge": "end",
            "requested_sec": 200.0,
            "mode": "exact",
            "confirmed": False,
            "set_against": set_against,
        }
    )


def draft(spec: Any, set_against: str, analysed: str = SHA) -> Any:
    analyses = [SimpleNamespace(reference="r0", sha256=analysed)]
    return SimpleNamespace(
        spec=spec,
        music=SimpleNamespace(analyses=analyses),
        overlay=of_records([pin(set_against)]),
    )


def test_a_pin_set_against_the_recorded_file_is_not_to_listen_to_again() -> None:
    assert applied_form.unconfirmed(draft(SPEC, SHA)) is False


def test_an_unconfirmed_pin_whose_file_changed_is_to_listen_to_again() -> None:
    assert applied_form.unconfirmed(draft(SPEC, OTHER)) is True


@pytest.mark.parametrize(
    "spec",
    [
        {"tracks": [{**SPEC["tracks"][0], "end_pin": {"mode": "loose"}}], "crossfades": []},
        {"tracks": [None], "crossfades": []},  # a piece that is no object
        {"crossfades": []},  # no pieces
    ],
)
def test_a_spec_that_does_not_read_fails_closed(spec: dict[str, Any]) -> None:
    assert applied_form.unconfirmed(draft(spec, SHA)) is True


def test_an_error_inside_the_check_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object) -> object:
        raise ValueError("does not read")

    monkeypatch.setattr(applied_form, "requests_moved", broken)
    assert applied_form.unconfirmed(draft(SPEC, SHA)) is True
