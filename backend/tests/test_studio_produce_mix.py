"""SR C7 (ADR 0036 section 6, ADR 0005 section 7): ``produce_outputs`` for the SwedeBeat mix and its
MOWL attach, on the fakes (the SwedeBeat gateway; the MOWL library's two calls answered by an
in-test listing), with the render child stood in (``studiooutputs.Renders``); never the network.

- the mix: each track downloaded fresh and checked against the analysed sha256, rendered once
  from the stored plan, merge-published with exactly those bytes, its URL stored;
- a fresh download whose sha256 is not the analysed one is ``music_changed``: nothing rendered
  or sent; a second render with another sha256 is ``render_not_reproducible``, nothing sent;
- a merge in doubt is never rendered or sent again; Check again shows the MOWL entry with the
  mix's unique name (never taken); the user's Confirm settles it;
- an entry whose URL the merge could not have returned (empty, ``http:``, a query) is never
  shown or confirmed: Confirm is ``nothing_to_confirm`` and the merge stays in doubt;
- MOWL attaches only the entry with the URL's object key and the duration; an entry with the
  name alone is never attached: ``published_not_listed`` with Check again.
"""

import json
from typing import Any
from urllib.parse import urlsplit

import pytest
from mowl import api as mowl_api

from classbeat import connections
from classbeat.swedebeat_studio import outputs_mowl
from ctx import make_context
from gatewaycore import MERGE_PATH
from mowlfake import TOKEN
from studiooutputs import (
    Renders,
    act,
    operations,
    outputs_on,  # noqa: F401 - fixture
    produce,
    renders_fixture,  # noqa: F401 - the ``renders`` fixture
    rows,
    sent,
    view,
)
from studiotools import StudioEnv, StudioRider
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")


class Listing:
    """The user's MOWL mix listing as ``mowl.api.swedebeat_mixes`` answers it, and the attaches."""

    def __init__(self, studio: StudioEnv) -> None:
        self.studio = studio
        self.named_only = False  # list every merged mix under its name and another URL only
        self.listed_url: str | None = None  # list every merged mix under this URL instead
        self.extra: list[dict[str, Any]] = []
        self.attached: list[str] = []

    def duration_ms(self) -> int:
        found = self.studio.songs.sql(
            "SELECT detail->>'duration_sec' FROM class_outputs WHERE output = 'swedebeat_mix'"
        ).scalar()
        return int(float(found or 0) * 1000)

    def mixes(self, creds: Any) -> list[dict[str, Any]]:
        listed = [
            {"ID": f"mix-{n}", "Name": m["name"],
             "URL": self.url_of(m),
             "DurationInMilliseconds": self.duration_ms()}
            for n, m in enumerate(self.studio.gateway.mixes)
        ]  # fmt: skip
        return [*self.extra, *listed]

    def url_of(self, mix: dict[str, Any]) -> str:
        if self.listed_url is not None:
            return self.listed_url
        return "https://other.example/a.mp3" if self.named_only else mix["url"]

    def attach(self, creds: Any, mix_id: str) -> dict[str, Any]:
        self.attached.append(mix_id)
        return {"PlaylistID": 7000 + len(self.attached)}


@pytest.fixture
def listing(studio: StudioEnv, monkeypatch: pytest.MonkeyPatch) -> Listing:
    made = Listing(studio)
    monkeypatch.setattr(mowl_api, "swedebeat_mixes", made.mixes)
    monkeypatch.setattr(mowl_api, "attach_swedebeat", made.attach)
    monkeypatch.setattr(outputs_mowl, "POLL_SECONDS", (0.0, 0.0))
    return made


async def with_mowl(studio: StudioEnv, rider: StudioRider) -> None:
    ctx = make_context(rider.user_id)
    await connections.save(studio.songs.factory, studio.songs.ring, ctx, "mowl", {"token": TOKEN})


def references(studio: StudioEnv, current: Any) -> list[str]:
    spec = studio.songs.revision(current.current_revision_id).music["mix_spec"]
    return [t["reference"] for t in spec["tracks"]]


async def test_the_mix_is_rendered_once_and_attached_by_its_url(
    studio: StudioEnv, renders: Renders, listing: Listing
) -> None:
    rider, class_id, _, current = await designed(studio)
    await with_mowl(studio, rider)
    listing.extra.append(  # another mix of the user's: never taken
        {"ID": "mix-other", "Name": "other", "URL": "https://elsewhere.example/x.mp3",
         "DurationInMilliseconds": 1000}
    )  # fmt: skip
    downloads_before = len([r for r in studio.gateway.requests if r.path.startswith("/tracks/")])
    done = await produce(studio, rider, class_id, current, "swedebeat_mix", "mowl")
    assert (done.state, done.error_category) == ("completed", None)
    wanted = references(studio, current)
    fresh = [r.path for r in studio.gateway.requests if r.path.startswith("/tracks/")]
    assert len(fresh) - downloads_before == len(wanted)  # each track downloaded fresh
    (plan,) = renders.plans  # rendered once
    assert len(plan) == len(wanted)
    (merge,) = sent(studio.gateway, "POST", MERGE_PATH)
    (mix,) = studio.gateway.mixes
    assert merge.headers["content-type"] == "audio/mpeg"
    assert "x-tracks" in merge.headers
    assert "confirm_irreversible=true" in merge.query
    by_output = {r.output: r for r in rows(studio, rider.user_id)}
    stored = by_output["swedebeat_mix"]
    assert stored.state == "done"
    assert mix["sha256"] == stored.detail["render_sha256"]  # exactly the rendered bytes
    assert mix["name"] == stored.detail["name"]
    assert stored.detail["mix_url"] == mix["url"]
    assert by_output["mowl"].state == "done"
    assert listing.attached == ["mix-0"]  # the URL's entry, never the decoy
    shown = await view(rider, class_id)
    assert (shown["swedebeat_mix"]["state"], shown["mowl"]["state"]) == ("done", "done")
    assert mix["url"] not in str(shown)
    assert urlsplit(mix["url"]).path not in str(shown)
    # At most once: another approval renders and sends nothing.
    again = await produce(studio, rider, class_id, current, "swedebeat_mix", "mowl")
    assert again.state == "completed"
    assert (len(renders.plans), len(studio.gateway.mixes), listing.attached) == (1, 1, ["mix-0"])


async def test_a_changed_track_is_music_changed_with_nothing_sent(
    studio: StudioEnv, renders: Renders
) -> None:
    rider, class_id, _, current = await designed(studio)
    first = references(studio, current)[2]
    studio.gateway.add_audio(first, studio.gateway.audio[first] + b"\x00 re-encoded")
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("failed", "music_changed")
    assert renders.plans == []
    assert sent(studio.gateway, "POST", MERGE_PATH) == []
    assert studio.gateway.mixes == []


async def test_a_second_render_with_another_sha256_is_not_reproducible(
    studio: StudioEnv, renders: Renders
) -> None:
    rider, class_id, _, current = await designed(studio)
    renders.vary = True  # every render's bytes differ
    studio.gateway.inject("connect_error", MERGE_PATH)  # the first merge never leaves
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("failed", "render_not_reproducible")
    assert len(renders.plans) == 2  # the retry rendered again and refused the new bytes
    assert studio.gateway.mixes == []
    shown = (await view(rider, class_id))["swedebeat_mix"]
    assert (shown["state"], shown["category"]) == ("failed", "render_not_reproducible")


async def test_a_merge_in_doubt_is_confirmed_by_the_user_from_the_named_entry(
    studio: StudioEnv, renders: Renders, listing: Listing
) -> None:
    rider, class_id, _, current = await designed(studio)
    await with_mowl(studio, rider)
    studio.gateway.inject("status", MERGE_PATH, status=502)  # after the work: it may exist
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("failed", "uncertain_write")
    assert len(studio.gateway.mixes) == 1
    shown = (await view(rider, class_id))["swedebeat_mix"]
    assert (shown["state"], shown["actions"]) == ("in_doubt", ["check_again", "create_another"])
    # In doubt is never rendered or sent again.
    await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (len(renders.plans), len(studio.gateway.mixes)) == (1, 1)
    body = {"revision_id": str(current.current_revision_id)}
    nothing = await act(rider, class_id, "swedebeat_mix", "confirm", body)
    assert (nothing.status_code, nothing.json()["detail"]["reason"]) == (409, "nothing_to_confirm")
    checked = await act(rider, class_id, "swedebeat_mix", "check-again", body)
    assert checked.status_code == 200, checked.text
    found = checked.json()
    assert found["state"] == "in_doubt"  # shown, never taken
    assert found["candidate"]["name"] == studio.gateway.mixes[0]["name"]
    assert "confirm" in found["actions"]
    assert "url" not in found["candidate"]
    confirmed = await act(rider, class_id, "swedebeat_mix", "confirm", body)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "done"
    (row,) = rows(studio, rider.user_id)
    assert row.detail["mix_url"] == studio.gateway.mixes[0]["url"]
    assert listing.attached == []  # MOWL was not chosen


@pytest.mark.parametrize(
    "url", ["", "http://mixes.example/a.mp3", "https://mixes.example/a.mp3?x=1", " https://m.e/a"]
)
async def test_a_listed_url_that_is_no_mix_url_is_never_confirmed(
    studio: StudioEnv, renders: Renders, listing: Listing, url: str
) -> None:
    rider, class_id, _, current = await designed(studio)
    await with_mowl(studio, rider)
    studio.gateway.inject("status", MERGE_PATH, status=502)  # after the work: it may exist
    done = await produce(studio, rider, class_id, current, "swedebeat_mix")
    assert (done.state, done.error_category) == ("failed", "uncertain_write")
    listing.listed_url = url  # the unique name, under a URL no merge returns
    body = {"revision_id": str(current.current_revision_id)}
    checked = await act(rider, class_id, "swedebeat_mix", "check-again", body)
    assert checked.status_code == 200, checked.text
    assert "candidate" not in checked.json()  # left out of the listing: nothing shown
    assert checked.json()["actions"] == ["check_again", "create_another"]
    # A candidate stored before the check (an older row) is refused by Confirm itself.
    candidate = {"name": studio.gateway.mixes[0]["name"], "url": url, "duration_sec": 1.0}
    studio.songs.sql(
        "UPDATE class_outputs SET detail = detail || jsonb_build_object('candidate', "
        "CAST(:c AS jsonb)) WHERE owner_id = :o AND output = 'swedebeat_mix'",
        c=json.dumps(candidate), o=rider.user_id,
    )  # fmt: skip
    refused = await act(rider, class_id, "swedebeat_mix", "confirm", body)
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (409, "nothing_to_confirm")
    (row,) = rows(studio, rider.user_id)
    assert (row.state, row.external_id, row.detail.get("mix_url")) == ("in_doubt", None, None)
    (merge,) = [o for o in operations(studio, rider.user_id) if o.kind == "swedebeat_merge_publish"]
    assert merge.state == "uncertain"


async def test_a_name_only_entry_is_never_attached(
    studio: StudioEnv, renders: Renders, listing: Listing
) -> None:
    rider, class_id, _, current = await designed(studio)
    await with_mowl(studio, rider)
    listing.named_only = True  # MOWL lists the mix's unique name with another URL only
    done = await produce(studio, rider, class_id, current, "swedebeat_mix", "mowl")
    assert done.state == "completed"
    assert listing.attached == []
    shown = await view(rider, class_id)
    assert shown["swedebeat_mix"]["state"] == "done"
    assert (shown["mowl"]["state"], shown["mowl"]["actions"]) == (
        "published_not_listed", ["check_again"],
    )  # fmt: skip
    body = {"revision_id": str(current.current_revision_id)}
    checked = await act(rider, class_id, "mowl", "check-again", body)
    assert checked.json()["state"] == "published_not_listed"  # still nothing to take
    assert listing.attached == []
    assert len(studio.gateway.mixes) == 1  # never published again
