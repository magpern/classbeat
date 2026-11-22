"""The proposal a studio draft's mix choices apply to, read back (ADR 0036 amendment B, B4, #1369).
Shared by the read-only preview, the ``apply_mix_choices`` job and the output check of B9.

- ``resolve``: from a draft (the current one, or an applied draft whose proposal is wanted), the
  nearest draft on the same ``song_list`` whose origin is in ``mix_proposal_policy``'s allowlist,
  following ``parent_id`` within the class; ``None`` when there is none (``no_proposal``);
- ``load``: that draft's content and music, its stored hash re-checked (``None`` when it no longer
  holds or is not a studio draft: a revision is immutable, so that is never normal);
- ``studio_music``: the approved list's analyses **exactly as the proposal recorded them**, from
  the user's cache in the caller's transaction (no download, no network, no connection held);
  ``None`` when one is gone (``music_changed``);
- ``for_draft``: all of the above for one draft, or the reason it has none;
- ``proposal_of``: the pure ``mix_choice_model.Proposal`` of the list order, the proposal's
  stored spec and the list's ``target_sec``.

Every statement filters on ``owner_id`` and ``class_id`` as well as running under row security.
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design import revision
from classbeat.design.studio_revision_model import StudioRevisionMusic, wanted
from classbeat.design.track_cache import cached_in, recorded
from classbeat.jobs import JobFailure
from classbeat.studio import mix_proposal_policy
from classbeat.studio.mix_choice_model import Proposal
from classbeat.swedebeat_studio import song_list
from classbeat.swedebeat_studio.draft import StudioMusic
from classbeat.swedebeat_studio.song_list_model import SOURCE

_STEP = text(
    "SELECT kind, parent_id, provenance ->> 'origin' AS origin, music ->> 'source' AS source, "
    "music ->> 'song_list_id' AS song_list_id FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id"
)
_DRAFT = text(
    "SELECT content, content_hash, music FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND kind = 'draft'"
)


@dataclass(frozen=True)
class ProposalDraft:
    revision_id: UUID
    content: dict[str, Any]
    music: StudioRevisionMusic

    @property
    def spec(self) -> dict[str, Any]:
        return self.music.mix_spec.model_dump(mode="json")


async def resolve(
    db: AsyncSession, owner_id: UUID, class_id: UUID, start: UUID, song_list_id: UUID
) -> UUID | None:
    """The proposal of the draft ``start`` (``start`` itself when it is one), else ``None``."""
    at: UUID | None = start
    for _ in range(mix_proposal_policy.MAX_CHAIN):
        if at is None:
            return None
        params = {"owner_id": owner_id, "class_id": class_id, "id": at}
        row = (await db.execute(_STEP, params)).first()
        if row is None:
            return None
        decided = mix_proposal_policy.step(
            studio_draft=row.kind == "draft" and row.source == SOURCE,
            same_list=row.song_list_id == str(song_list_id),
            origin=row.origin,
        )
        if decided == "proposal":
            return at
        if decided == "stop":
            return None
        at = row.parent_id
    return None


async def load(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID
) -> ProposalDraft | None:
    params = {"owner_id": owner_id, "class_id": class_id, "id": revision_id}
    row = (await db.execute(_DRAFT, params)).first()
    if row is None or not isinstance(row.content, dict):
        return None
    content: dict[str, Any] = row.content  # pyright: ignore[reportUnknownVariableType]
    try:
        music = StudioRevisionMusic.model_validate_json(json.dumps(row.music))
    except ValidationError:
        return None
    spec = music.mix_spec.model_dump(mode="json")
    if revision.studio_hash(content, spec) != bytes(row.content_hash):
        return None
    return ProposalDraft(revision_id, content, music)


def approved(songs: song_list.StudioList) -> list[str | None]:
    """The approved list's references in its order, ``None`` for a gap."""
    return [s.swedebeat.reference if s.swedebeat else None for s in songs.content.songs]


async def studio_music(
    db: AsyncSession, owner_id: UUID, songs: song_list.StudioList, proposal: ProposalDraft
) -> StudioMusic | None:
    """The list's analyses the proposal recorded, from the cache; ``None`` when one is gone."""
    references = [r for r in approved(songs) if r is not None]
    if len(references) != len(songs.content.songs):
        return None  # a gap: no analyses to read (``applied`` refuses ``list_has_gap``)
    maps = await cached_in(db, owner_id, references, recorded(wanted(proposal.music, references)))
    if any(r not in maps for r in references):
        return None
    return StudioMusic.of(songs.content, [maps[r] for r in references])  # ``internal``: broken


def proposal_of(proposal: ProposalDraft, songs: song_list.StudioList) -> Proposal:
    return Proposal(approved(songs), proposal.spec, songs.content.target_sec)


@dataclass(frozen=True)
class Resolved:
    """The proposal of a draft with the analyses it was designed on."""

    draft: ProposalDraft
    music: StudioMusic
    proposal: Proposal


async def for_draft(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    start: UUID,
    list_id: UUID,
    songs: song_list.StudioList,
) -> Resolved | str:
    """The proposal of the draft ``start`` on ``songs`` (its approved list), or a reason:
    ``no_proposal``, ``internal`` (its stored hash no longer holds) or ``music_changed``.
    ``list_id`` is the id of ``songs``, the draft's ``song_list``."""
    found = await resolve(db, owner_id, class_id, start, list_id)
    if found is None:
        return mix_proposal_policy.NO_PROPOSAL
    loaded = await load(db, owner_id, class_id, found)
    if loaded is None or loaded.music.song_list_id != list_id:
        return "internal"
    try:
        music = await studio_music(db, owner_id, songs, loaded)
    except JobFailure:
        return "internal"  # every reference was found: the list and its analyses disagree
    if music is None:
        return "music_changed"
    return Resolved(loaded, music, proposal_of(loaded, songs))
