"""``design.edited_base_calc``: which base revisions an AI change refuses (#1424). Pure."""

import pytest

from classbeat.design.edited_base_calc import SPOTIFY, USER_EDIT, is_edited_spotify

RECORD = {"type": "lock", "segment_kind": "main", "span": [0, 60000], "scope": "all"}


@pytest.mark.parametrize(
    ("source", "origin", "overlay", "edited"),
    [
        (SPOTIFY, USER_EDIT, None, True),  # a user_edit revision, even with nothing recorded
        (SPOTIFY, USER_EDIT, {"records": []}, True),
        (SPOTIFY, "design_draft", {"records": [RECORD]}, True),  # any record in the overlay
        (SPOTIFY, "design_draft", None, False),  # an unedited draft
        (SPOTIFY, "revise_draft", {"records": []}, False),
        (SPOTIFY, "design_draft", {"records": "x"}, False),
        ("mowl_swedebeat_mix", USER_EDIT, {"records": [RECORD]}, False),  # the studio's own path
        ("none", USER_EDIT, None, False),
        (None, USER_EDIT, None, False),
    ],
)
def test_only_an_edited_spotify_draft_is_refused(
    source: object, origin: object, overlay: object, edited: bool
) -> None:
    assert is_edited_spotify(source, origin, overlay) is edited
