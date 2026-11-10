"""The hand-written kind lists (ADR 0029 section 5, MA1b-3): every registered job kind is either a
design kind whose input carries the class's first brief, or one that never does, and the
``interpret_request`` kind is the second on purpose. The SQL that names kinds by hand reads them
from ``classbeat.classes.kinds`` or names exactly the design kinds, and no list of revision
origins needs the new kind (it stores no revision)."""

import inspect
import typing

import classbeat.saving
import classbeat.songs
import classbeat.swedebeat_handoff
import classbeat.swedebeat_match  # noqa: F401 - these four imports register their kinds
from classbeat.agent.records import Provenance
from classbeat.classes import kinds
from classbeat.design import interpret_kind, revision
from classbeat.design import sport as sport_module
from classbeat.jobs import KINDS


def test_every_registered_kind_is_classified() -> None:
    names = set(KINDS.names())
    assert names == set(kinds.DESIGN_KINDS) | set(kinds.NO_FIRST_BRIEF)
    assert not set(kinds.DESIGN_KINDS) & set(kinds.NO_FIRST_BRIEF)


def test_the_interpret_kind_carries_no_brief_and_is_not_a_design_kind() -> None:
    assert interpret_kind.KIND == kinds.INTERPRET_KIND
    assert "brief" not in interpret_kind.InterpretRequestInput.model_fields
    assert kinds.INTERPRET_KIND not in kinds.DESIGN_KINDS
    for name in kinds.DESIGN_KINDS:
        kind = KINDS.get(name)
        assert kind is not None
        assert "brief" in kind.input_model.model_fields


def test_the_hand_written_sql_names_the_design_kinds() -> None:
    listed = kinds.sql_list(kinds.DESIGN_KINDS)
    assert listed in kinds.CLASS_SPORT_SQL
    assert "c.interpretation -> 'brief'" in kinds.CLASS_SPORT_SQL
    assert listed in str(sport_module._CLASS_SPORT)  # pyright: ignore[reportPrivateUsage]


def test_no_revision_origin_list_needs_the_interpret_kind() -> None:
    """``agent.records`` and ``design.revision`` list the kinds that store a revision; an
    interpretation stores none (``interpretation_row``; ``test_interpret_job.py`` checks that no
    revision row exists after it), so it is in neither."""
    origin = typing.get_type_hints(Provenance)["origin"]
    origins = {a for arg in typing.get_args(origin) for a in typing.get_args(arg)}
    assert kinds.INTERPRET_KIND not in origins
    assert kinds.INTERPRET_KIND not in inspect.getsource(revision)
