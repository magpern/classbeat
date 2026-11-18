"""The composer facts the pool stage's first message gets (#1188 review, non-blocking 3): the
sketch's slot roles and the refs of the favoured and pinned songs, as integers, role codes and
fixed text. A planted playlist name, title, artist, album, Spotify id or cover never appears in
what the backend adds. The ``kept_shape`` revision check refuses a reordered class. Pure."""

import json

from classbeat.agent import PickPlan, PoolContext, SearchRegistry
from classbeat.agent.brief import Brief
from classbeat.agent.registry import Entry
from classbeat.evals.shape_checks import kept_shape
from classbeat.songs.composer_pick import pool_message
from classbeat.songs.composer_sources import Favoured, Pinned

PLANTED = ("PLANTED-TITLE", "PLANTED-ARTIST", "PLANTED-ALBUM", "PlantedSpotifyId000001")
COVER = "https://i.scdn.co/image/" + "ab" * 20


def entry(ref: int) -> Entry:
    return Entry(
        ref, "PLANTED-TITLE", "PLANTED-ARTIST", "PLANTED-ALBUM", 2001, 240, PLANTED[3], COVER
    )


def test_the_facts_are_numbers_roles_and_fixed_text_only() -> None:
    brief = Brief.model_validate({"length_min": 30, "intensity": "moderate"})
    ctx = PoolContext(PickPlan.build(brief), SearchRegistry(), lambda q: [])
    favoured = Favoured((entry(3), entry(7)), (), {PLANTED[3]: 0})
    pinned = Pinned(((0, entry(5)),), ())
    slots = ["warmup", "climb", "cooldown"]
    message = pool_message(ctx, slots, favoured, pinned)
    added = message.removeprefix(ctx.first_message())
    for planted in (*PLANTED, COVER, "Mine", "2001"):
        assert planted not in added
    facts = json.loads(added[added.index("{") :])
    assert facts == {"sketch_slots": slots, "favoured_refs": [3, 7], "pinned_refs": [5]}


def test_kept_shape_refuses_a_reordered_or_shortened_class() -> None:
    base = {"segments": [{"kind": "warmup"}, {"kind": "main"}, {"kind": "cooldown"}]}
    assert kept_shape(base, base)[0]
    reordered = {"segments": [{"kind": "main"}, {"kind": "warmup"}, {"kind": "cooldown"}]}
    dropped = {"segments": [{"kind": "warmup"}, {"kind": "cooldown"}]}
    assert not kept_shape(reordered, base)[0]
    assert not kept_shape(dropped, base)[0]
