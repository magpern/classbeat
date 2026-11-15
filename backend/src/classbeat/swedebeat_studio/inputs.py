"""The input of ``studio_songs`` (ADR 0036 section 3).

``{class_id | title, brief, action, seed?: {song_list_revision_id, position?},
spotify_playlist_id?, spotify_connection_id, swedebeat_connection_id}``.

- ``spotify_playlist_id``: the user's own Spotify playlist as the candidates instead of a search
  (``pick`` and ``swap_anyway`` only). It is read with the user's connection and never modified.

- ``CONNECTIONS = ("spotify", "swedebeat")``: no MOWL connection (a studio class needs none, ADR
  0036 section 2). The jobs API refuses the job, and later its approval, unless both ids are the
  user's current connections.
- ``action``:
  - ``pick`` (the default): a new list from a Spotify search; no seed.
  - ``swap_anyway``: the user's explicit choice after a ``suspected_unavailable`` list (its id in
    ``seed``): a new pick that swaps even when every song is missing.
  - ``undo``: the swap at ``seed.position`` of the seed list undone, its original back as a gap.
    No model and no catalogue call.
  - ``another``: another song for ``seed.position`` (a swapped slot or a gap) of the seed list.
  Every action but ``pick`` names an existing class and its seed; ``undo`` and ``another`` name a
  position, the others none. ``admit`` checks the seed against the class.
- ``brief``: the class's, cycling only, refused when it would not fit ``classes.brief``.

No field is a server field beyond ``ModelJobInput``'s, and nothing in the input reaches a request:
the pool, the playlists, the matched titles and every id written come from the user's own rows.
"""

from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent.brief import Brief
from classbeat.agent.schemas import MAX_SONGS
from classbeat.design import revision
from classbeat.jobs.inputs import ModelJobInput, jsonb_size

Action = Literal["pick", "swap_anyway", "undo", "another"]
POSITIONED: tuple[str, ...] = ("undo", "another")


class StudioSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_list_revision_id: UUID
    position: int | None = Field(default=None, ge=0, lt=MAX_SONGS)


class StudioSongsInput(ModelJobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("spotify", "swedebeat")

    brief: Brief
    action: Action = "pick"
    seed: StudioSeed | None = None
    # The user's own Spotify playlist as the candidates (read only, never modified), instead of a
    # search: only for a pick or a swap anyway.
    spotify_playlist_id: Annotated[str, Field(pattern=r"^[0-9A-Za-z]{22}$")] | None = None
    spotify_connection_id: UUID
    swedebeat_connection_id: UUID

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if jsonb_size(self.brief.stored()) > revision.MAX_BRIEF_BYTES:
            raise ValueError("the brief is too long")
        if self.brief.sport != "cycling":  # ADR 0018: running v1 has no song picking
            raise ValueError("song picking is for cycling classes only")
        if self.spotify_playlist_id is not None and self.action not in ("pick", "swap_anyway"):
            raise ValueError("only a pick reads the user's playlist")
        if self.action == "pick":
            if self.seed is not None:
                raise ValueError("a pick has no seed")
            return self
        if self.seed is None or self.class_id is None:
            raise ValueError("an action names its class and its list")
        if (self.seed.position is not None) != (self.action in POSITIONED):
            raise ValueError("undo and another name a position, the others none")
        return self

    @property
    def the_seed(self) -> StudioSeed:
        assert self.seed is not None  # noqa: S101 - the validator above
        return self.seed
