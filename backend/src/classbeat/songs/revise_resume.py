"""The ``resume`` mode of ``revise_songs``' ``admit`` (ADR 0008 section 6), split out of
``classbeat.songs.revise``: the receipt of the list a ``revise_songs`` job approved, the base it was
made for, the Spotify account and the unresolved-write rule. Each refusal writes nothing.
"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import connections
from classbeat.context import UserContext
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.operations import orphans
from classbeat.songs import admit as song_admit
from classbeat.songs.inputs import ReviseSongsInput


async def admit_resume(
    db: AsyncSession,
    ctx: UserContext,
    given: ReviseSongsInput,
    class_id: UUID,
    fields: dict[str, object],
    kind: str,
) -> Admitted | Refused:
    """The ``resume`` admission of ``revise_songs`` (``kind``): ``fields`` are the server fields
    ``admit`` already set; the answer adds the approved job's message and catalogue fields."""
    if given.resume is None:  # the input model refuses it already
        return Refused("receipt_invalid")
    account = await connections.spotify_account_in(db, ctx, given.spotify_connection_id)
    if account is None:
        return Refused("not_connected")
    found = await song_admit.receipt_valid(
        db, ctx.user_id, class_id, given.resume.song_list_revision_id, kind
    )
    if found is None:
        return Refused("receipt_invalid")
    try:
        approved = ReviseSongsInput.model_validate(found.approved_input)
    except ValidationError:
        return Refused("receipt_invalid")
    if approved.base_revision_id != given.base_revision_id:
        return Refused("stale_base")  # the list was made to change another version
    if approved.request_message_id is None:
        return Refused("receipt_invalid")
    if found.stored.account != account:
        return Refused("account_changed")
    if await orphans.playlist_unresolved(db, ctx, class_id):
        return Refused("write_unresolved")
    copied: dict[str, object] = {"request_message_id": approved.request_message_id}
    if approved.swedebeat_connection_id is not None:
        if approved.catalogue_replace is None or approved.catalogue_fact is None:
            return Refused("receipt_invalid")
        copied["catalogue_replace"] = approved.catalogue_replace
        copied["catalogue_fact"] = approved.catalogue_fact
        copied["swedebeat_connection_id"] = approved.swedebeat_connection_id
    return Admitted({**fields, **copied})
