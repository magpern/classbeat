"""Where ``classes``, ``class_revisions`` and ``beat_one_corrections`` rows may be inserted (ADR
0007 section 2 and its amendments of 2026-11-03, #536 and #535).

A revision is created only in a job's final fenced transaction, with one exception: the duplicate
route inserts revision 1 of the class it creates. A class is created only by the jobs API (a
design job's class) and by the duplicate route. A beat-one correction is copied by the duplicate
route to the class it creates (the beat-one amendment's item 8, DU3 #585); the corrections API
(BC4) adds its own site here when it lands. This guard reads the backend's source and fails for
an ``INSERT INTO`` one of these tables anywhere else, and for an allowed module that no longer
has one (so the list never goes stale). A new site is added here
with its reason, and reviewed.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "classbeat"

ALLOWED: dict[str, set[str]] = {
    # The jobs API's create transaction: a design job's new class (ADR 0005 section 2).
    "classes": {"jobs/user.py", "classes/duplicate.py"},
    "class_revisions": {
        "design/revision.py",  # design_draft and revise_draft, the final fenced store
        "songs/song_list.py",  # the song pick's song list, the final fenced store
        "swedebeat_match/revision.py",  # the Swedebeat match, the final fenced store
        "classes/duplicate.py",  # the one exception: revision 1 of the class it creates
    },
    "beat_one_corrections": {
        "classes/duplicate.py",  # the source's rows for the copied music, re-keyed to the copy
    },
}


def _sites(table: str) -> set[str]:
    pattern = re.compile(rf"insert\s+into\s+(?:public\.)?{table}\b", re.IGNORECASE)
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(SRC).as_posix())
    return found


def test_classes_are_inserted_only_by_the_jobs_api_and_the_duplicate_route() -> None:
    assert _sites("classes") == ALLOWED["classes"]


def test_revisions_are_inserted_only_by_final_stores_and_the_duplicate_route() -> None:
    assert _sites("class_revisions") == ALLOWED["class_revisions"]


def test_corrections_are_inserted_only_where_listed() -> None:
    assert _sites("beat_one_corrections") == ALLOWED["beat_one_corrections"]


def test_the_pattern_sees_a_planted_insert() -> None:
    pattern = re.compile(r"insert\s+into\s+(?:public\.)?classes\b", re.IGNORECASE)
    assert pattern.search('text("INSERT  INTO public.classes (id) VALUES (:id)")')
    assert pattern.search("insert into classes")
    assert not pattern.search("INSERT INTO class_messages (id)")
