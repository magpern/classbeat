"""A composer's fill writes the working copy twice in one job (the pool, then the chosen songs in
play order; ADR 0038 section 5), so a retried attempt must never "reuse" a write whose list the
playlist no longer holds (#1188 review B1, ``playlists.pool_keys``). On the fake Spotify and MOWL:
a retry after a transient failure past the second write, with the same pool and with a new pool
and the same choice, writes again and reaches the wait. Never the network or a model."""

from typing import Any
from uuid import UUID

import pytest

from classbeat.operations.kinds import SPOTIFY_REPLACE_KIND, spotify_purpose
from classbeat.playlists import common
from composertools import ComposerEnv, choose_turns, design_turns, fill_script, pool_turns
from pickfake import POOL_14
from songtools import POOL

pytestmark = pytest.mark.usefixtures("jenv")


def overloaded_design(pool: list[tuple[int, str]] | None = None) -> dict[str, Any]:
    """The pick (``pool``: the model's candidates), then the design stage overloaded: retried."""
    script = fill_script()
    script["turns"] = [
        *pool_turns(pool),
        *choose_turns(),
        {"only_in": "revise", "error": "overloaded"},
    ]
    return script


def item_writes(cenv: ComposerEnv) -> int:
    return sum(
        1 for s in cenv.songenv.api() if s.method in ("PUT", "POST") and s.path.endswith("/items")
    )


@pytest.mark.parametrize("new_pool", [False, True], ids=["same_pool", "new_pool_same_choice"])
async def test_a_retry_after_the_songs_write_writes_again_and_waits(
    cenv: ComposerEnv, new_pool: bool
) -> None:
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    made = await cenv.fill(rider, class_id)
    assert made.status_code == 201, made.text
    job_id = UUID(made.json()["job_id"])
    cenv.songenv.script = overloaded_design()
    first = await cenv.songenv.run(job_id)
    assert (first.state, first.error_category) == ("queued", None), first.error_category
    before = item_writes(cenv)
    retried = [*POOL_14, (14, "recovery")] if new_pool else None  # the same eight are chosen
    cenv.songenv.script = {
        **fill_script(),
        "turns": [*pool_turns(retried), *choose_turns(), *design_turns()],
    }
    row = await cenv.songenv.run(job_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    assert item_writes(cenv) > before  # the playlist is written again, never assumed
    keys = (
        cenv.sql(
            "SELECT idempotency_key FROM external_operations WHERE owner_id = :o "
            "AND kind = 'spotify_replace_items' ORDER BY created_at",
            o=rider.user_id,
        )
        .scalars()
        .all()
    )
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    ("suffix", "purpose"),
    [("", "pool"), (":1", "pool"), (":42", "pool"), (":0", None), (":x", None)],
)
def test_a_rewrite_key_is_still_a_pool_write(suffix: str, purpose: str | None) -> None:
    key = f"{UUID(int=1)}:pool:{'a' * 16}{suffix}"
    assert spotify_purpose(SPOTIFY_REPLACE_KIND, key) == purpose


async def test_a_crash_in_the_songs_write_never_lets_the_retry_trust_the_pool(
    cenv: ComposerEnv,
) -> None:
    """#1188 delta review: the songs write's call reached the playlist, then the worker crashed
    before the row ended (``uncertain``), so ``last_hash`` still names the pool. The retried
    attempt must write the pool again (the playlist holds the songs), not reuse it."""
    rider = await cenv.songenv.rider()
    class_id, _ = await cenv.sketched(rider)
    made = await cenv.fill(rider, class_id)
    job_id = UUID(made.json()["job_id"])
    cenv.songenv.script = overloaded_design()
    first = await cenv.songenv.run(job_id)
    assert first.state == "queued", first.error_category
    rows = cenv.sql(
        "SELECT * FROM external_operations WHERE owner_id = :o "
        "AND kind = 'spotify_replace_items' ORDER BY created_at",
        o=rider.user_id,
    ).all()
    assert len(rows) == 2  # the pool, then the songs
    cenv.sql(  # the songs write's row as a crash leaves it, long past its bound
        "UPDATE external_operations SET state = 'uncertain', "
        "pending_at = now() - interval '2 hours' WHERE id = :r",
        r=rows[1].id,
    )
    cenv.sql(  # and the pool row still named as what the playlist holds
        "UPDATE spotify_pool SET last_hash = :h WHERE owner_id = :o",
        h=common.ids_hash(tuple(POOL)),
        o=rider.user_id,
    )
    before = item_writes(cenv)
    cenv.songenv.script = fill_script()
    row = await cenv.songenv.run(job_id)
    assert row.state == "waiting_for_approval", (row.state, row.error_category)
    assert item_writes(cenv) >= before + 2  # the pool and the songs, both written again
