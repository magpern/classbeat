"""Update's read-first rule on a program read back from MOWL (ADR 0007 section 5, issue #485),
split out of ``classbeat.saving.update`` (CLAUDE.md, split on touch). Pure: no I/O.

``program_refusal`` answers ``(before_hash, refusal)``: the normalized state's hash of the program
as read (hex; None when it cannot be normalized) and ``program_missing``, ``program_other_sport`` or
``program_changed`` when the write must not go ahead, else None. The sport check comes before
``overwrite``: "Update anyway" replaces the user's changes, never the sport.
"""

from classbeat import mowl_state


def program_refusal(
    program: object, program_id: int, state_hash: bytes | None, sport: str, overwrite: bool
) -> tuple[str | None, str | None]:
    """The module docstring's rule for the saved row's ``program_id`` and recorded
    ``state_hash``; ``sport`` is the revision's (the class's)."""
    if mowl_state.program_id_of(program) != program_id:
        return None, "program_missing"  # an empty answer, or another program
    try:
        normal = mowl_state.normalized_program(program)
    except ValueError:
        normal = None
    before = None if normal is None else mowl_state.state_hash(normal).hex()
    found = mowl_state.program_sport(program)
    if found is None or found != sport:
        # Another sport, or one we cannot tell (missing or unknown ``ActivityTypeID``): the
        # library would rewrite it with this class's sport and bodies (issue #485).
        return before, "program_other_sport"
    if overwrite:
        return before, None
    if normal is None or normal["public"] is not False:
        return before, "program_changed"  # the library writes it private: changed in MOWL
    recorded = state_hash
    if recorded is None or before is None or bytes.fromhex(before) != recorded:
        return before, "program_changed"
    return before, None
