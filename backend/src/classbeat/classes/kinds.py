"""The job kinds the class reads name by hand, in one place (ADR 0018 s. 1, ADR 0029 s. 5).

A class's brief is stored on the class with its first design (``classes.brief``). Until then the
reads take it from the newest **design job** whose input carries it: ``DESIGN_KINDS``. The
``interpret_request`` kind is not one of them on purpose: its input holds the request, not a brief
(``input -> 'brief'`` is NULL for it), and what it read is on the class itself
(``classes.interpretation -> 'brief'``), which the class list's sport reads last. The other kinds
work on a class that already has a revision, and so a stored brief. ``tests/test_class_kinds.py``
holds every registered kind to one of these lists, so a new kind is classified when it is added.
"""

from typing import Final

DESIGN_KINDS: Final = ("design_draft", "design_with_songs")
SONG_DESIGN_KIND: Final = "design_with_songs"
INTERPRET_KIND: Final = "interpret_request"
# Kinds that never carry a class's first brief: a revision exists before they can run, or (the
# interpretation) the brief is read from the class row instead.
NO_FIRST_BRIEF: Final = (
    INTERPRET_KIND,
    "revise_draft",
    "refit_draft",
    "revise_songs",
    "save_workout",
    "update_workout",
    "swedebeat_match",
    "swedebeat_verify_mix",
)


def sql_list(kinds: tuple[str, ...]) -> str:
    """A constant SQL list of kind names (names are ``[a-z_]`` only, ``jobs.kinds.KIND_NAME``)."""
    if not all(k.replace("_", "").isalpha() and k.islower() for k in kinds):
        raise ValueError("a kind name is a-z and _")
    return "(" + ", ".join(f"'{k}'" for k in kinds) + ")"


# ADR 0018 section 1: the stored brief's sport, else (a class still in its first design) the newest
# design job's, else the interpretation's; anything but 'running' is cycling. ``c`` is the class.
CLASS_SPORT_SQL: Final = f"""CASE WHEN coalesce(c.brief, (SELECT j.input -> 'brief' FROM jobs j
            WHERE j.owner_id = c.owner_id AND j.class_id = c.id
                AND j.kind IN {sql_list(DESIGN_KINDS)}
            ORDER BY j.created_at DESC LIMIT 1),
        c.interpretation -> 'brief') ->> 'sport' = 'running'
        THEN 'running' ELSE 'cycling' END"""  # noqa: S608 - constants only
