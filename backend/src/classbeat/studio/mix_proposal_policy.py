"""Which studio draft is the **proposal** the user's mix choices apply to (ADR 0036 amendment B,
B4, #1369). Pure: no I/O.

The proposal is the nearest draft, the current one or an ancestor on the same ``song_list``, whose
provenance ``origin`` is in an **allowlist of model designs**: ``design_draft`` and
``revise_draft``, as the studio design and revise jobs store them (``swedebeat_studio/draft.py``
``build``). Every other origin (``mix_choices``, ``duplicate``, ``beat_one``, ``None`` and any
origin added later until this list names it) is not a proposal and is passed over, so
``mix_choice_policy.applied`` never runs on a derived spec. The walk stops, with no proposal, at a
revision that is no studio draft of the same list (another list, a ``song_list`` row, a draft of
another music source) and after ``MAX_CHAIN`` revisions.

``step`` is the one decision per revision of the walk; ``swedebeat_studio.mix_proposal`` reads the
rows and follows ``parent_id``.
"""

from typing import Final, Literal

NO_PROPOSAL: Final = "no_proposal"
PROPOSAL_ORIGINS: Final[frozenset[str]] = frozenset({"design_draft", "revise_draft"})
APPLIED_ORIGIN: Final = "mix_choices"
MAX_CHAIN: Final = 500  # revisions walked; a class never holds near this many on one list

Step = Literal["proposal", "pass", "stop"]


def is_proposal(origin: object) -> bool:
    """Whether a draft of this origin is a model design (the allowlist; anything else is not)."""
    return isinstance(origin, str) and origin in PROPOSAL_ORIGINS


def step(*, studio_draft: bool, same_list: bool, origin: object) -> Step:
    """One revision of the walk from the starting draft towards the root: ``proposal`` (found),
    ``pass`` (follow its parent) or ``stop`` (no proposal on this chain)."""
    if not studio_draft or not same_list:
        return "stop"
    return "proposal" if is_proposal(origin) else "pass"
