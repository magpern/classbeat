"""SD-P7a (#1304; ADR 0040 sections 6 and 9): ``design_draft``, ``revise_draft`` and
``refit_draft`` on a SwedeBeat mix whose map spotdown made, through the real jobs API and worker
side on the fakes (``test_swedebeat_design``'s ``MixEnv``, the fake spotdown in process).

- a class designed on spotdown's map records the analyser and the analysis's provenance with its
  revision (plan section 4a: per class) and nothing the model is sent names the mix;
- switched off afterwards, a revise and a refit read the row the class was designed on
  (``prefer``): no fetch, the same map, the same recorded analysis, never ``music_changed``;
- with spotdown failing, the design is byte-identical to one with spotdown off.
"""

import asyncio
import sys
from typing import Any
from uuid import UUID

import pytest

from classbeat.design import track_spotdown
from classbeat.design.refit import KIND as REFIT_KIND
from classbeat.design.refit import REFIT_DRAFT
from classbeat.design.revise import KIND as REVISE_KIND
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.spotdown import studio_tracks_config
from classbeat.spotdown.studio_tracks_config import TracksConfig
from mowlfake import FakeMowl
from optools import TOKEN_A
from spotdownfake import FakeSpotdown, spotdown
from test_spotdown_mix_policy import mix_document
from test_swedebeat_design import (
    MIX_ID,
    MIX_NAME,
    SECONDS,
    MixEnv,
    add_mix,
    create,
    design_body,
    env,
    menv,
    revise_script,
    revisions,
    rider,
    work,
)

__all__ = ["env", "menv", "spotdown", "work"]  # the fixtures

pytestmark = pytest.mark.usefixtures("jenv", "mowl_api", "spotdown_slot_by_default")
KEY = bytes(range(32))
SONGS = [(0.0, 75.0, 124.0), (75.0, SECONDS, 126.0)]


@pytest.fixture(autouse=True)
def mix_documents(spotdown: FakeSpotdown, monkeypatch: pytest.MonkeyPatch) -> Any:
    """spotdown's analysis of the 150 s fake mix, naming the bytes it was asked for."""
    real = spotdown.analysis

    def analysis(analysis_id: str) -> dict[str, Any]:
        sha = real(analysis_id)["provenance"]["audio"]["sha256"]
        doc = mix_document(SONGS, sha=sha)
        doc["analysis_id"] = analysis_id
        return doc

    async def pause(*_args: object) -> None:
        return None

    monkeypatch.setattr(spotdown, "analysis", analysis)
    monkeypatch.setattr(track_spotdown, "pause", pause)
    studio_tracks_config.configure(TracksConfig(KEY))
    yield
    studio_tracks_config.configure(None)


def ask(kind: str, base: Any, browser: Any, **extra: Any) -> dict[str, Any]:
    body = {
        "class_id": str(base.class_id),
        "base_revision_id": str(base.id),
        "mowl_connection_id": str(browser.connection_id),
        **extra,
    }
    return {"kind": kind, "input": body}


async def designed(browsers: Any, menv: MixEnv, mowl_api: FakeMowl, token: str) -> Any:
    browser = await rider(browsers, menv, token)
    add_mix(mowl_api, token)
    return browser


async def test_a_class_designed_on_spotdowns_map_keeps_it_when_spotdown_is_gone(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl, spotdown: FakeSpotdown
) -> None:
    browser = await designed(browsers, menv, mowl_api, TOKEN_A)
    created = await create(browser, design_body(browser))
    assert created.status_code == 201, created.text
    job_id = UUID(created.json()["id"])
    await menv.run_next()
    row = menv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    [stored] = menv.ops.sql(
        "SELECT map, analyser, provenance FROM swedebeat_mix_maps WHERE owner_id = :o",
        o=browser.user_id,
    ).all()
    assert stored.analyser == "spotdown"
    [base] = revisions(menv, job_id)
    assert base.music["analyser"] == "spotdown"
    assert base.music["provenance"] == stored.provenance
    assert base.music["provenance"]["timing"]["class"] == "exact"
    assert MIX_ID not in str(base.music["provenance"])
    sent = menv.sent()
    for value in (MIX_NAME, MIX_ID):
        assert value not in sent
    assert all(MIX_ID not in s.query and MIX_NAME not in s.query for s in spotdown.seen)

    # Not installed any more: the revise reads the row the class was designed on (a cache hit).
    studio_tracks_config.configure(None)
    menv.scripts, menv.providers = [revise_script()], []
    revised = await create(browser, ask(REVISE_KIND, base, browser, request="Harder push"))
    assert revised.status_code == 201, revised.text
    fetches, calls = len(menv.media.requests), len(spotdown.seen)
    await menv.run_next()
    revise_id = UUID(revised.json()["id"])
    assert menv.ops.row(revise_id).state == "completed"
    [new] = revisions(menv, revise_id)
    assert new.music == base.music  # the same map, analyser and provenance
    assert (len(menv.media.requests), len(spotdown.seen)) == (fetches, calls)

    # A refit of the revised (current) draft too: the same fingerprint, never ``music_changed``.
    for song in new.music["songs"]:
        menv.ops.sql(
            "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
            "VALUES (:o, :c, :f, :s, 1, :b)",
            o=browser.user_id, c=base.class_id, f=base.music["fingerprint"], s=song["song"],
            b=song["bpm"],
        )  # fmt: skip
    made = await create(browser, ask(REFIT_KIND, new, browser))
    assert made.status_code == 201, made.text
    menv.configure()
    registry = KindRegistry()
    registry.register(REFIT_DRAFT)
    lease = menv.ops.app.settings.job_lease_seconds
    claimed = await queue.claim(menv.ops.factory, "sd-w", lease, [REFIT_KIND])
    assert claimed is not None
    await execute(
        Execution(claimed), menv.ops.factory, registry, RunConfig(lease_seconds=lease),
        asyncio.Event(),
    )  # fmt: skip
    refit = menv.ops.row(UUID(made.json()["id"]))
    assert (refit.state, refit.error_category) == ("completed", None)  # never music_changed
    assert len(menv.media.requests) == fetches
    [refitted] = revisions(menv, UUID(made.json()["id"]))
    assert (refitted.music["analyser"], refitted.music["fingerprint"]) == (
        "spotdown",
        base.music["fingerprint"],
    )


@pytest.mark.parametrize(
    ("end", "category"),
    [
        ("processing_failed:decode_error", "analysis_failed"),
        ("provider_error", "analysis_unavailable"),
    ],
)
async def test_with_spotdown_failing_the_design_ends_with_its_category_and_no_draft(
    browsers: Any,
    menv: MixEnv,
    mowl_api: FakeMowl,
    spotdown: FakeSpotdown,
    end: str,
    category: str,
) -> None:
    spotdown.end = end
    browser = await designed(browsers, menv, mowl_api, TOKEN_A)
    created = await create(browser, design_body(browser))
    await menv.run_next()
    row = menv.ops.row(UUID(created.json()["id"]))
    # A retryable ending (analysis_unavailable) goes back to the queue with its attempts left.
    ended = ("queued", None) if category == "analysis_unavailable" else ("failed", category)
    assert (row.state, row.error_category) == ended
    assert spotdown.seen  # spotdown was asked, and failed
    assert revisions(menv, row.id) == []
    assert (
        menv.ops.sql(
            "SELECT count(*) FROM swedebeat_mix_maps WHERE owner_id = :o", o=browser.user_id
        ).scalar_one()
        == 0
    )


async def test_admit_counts_a_spotdown_row_as_a_cached_mix_and_a_missing_row_not(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    """#1353: only a current ``spotdown`` row is a hit, so only it skips the daily limit; with the
    row gone (30 days, the cap) the mix is a new analysis (#1357: there is no other analyser)."""
    from classbeat.design import mixlimit
    from test_swedebeat_design import plant_analyses

    browser = await designed(browsers, menv, mowl_api, TOKEN_A)
    created = await create(browser, design_body(browser))
    await menv.run_next()
    assert menv.ops.row(UUID(created.json()["id"])).state == "completed"
    owner = browser.user_id
    plant_analyses(menv, owner, mixlimit.ANALYSES_PER_DAY - 1)
    assert menv.ops.sql(
        "SELECT analyser FROM swedebeat_mix_maps WHERE owner_id = :o", o=owner
    ).scalars().all() == ["spotdown"]
    cached = await create(browser, design_body(browser))
    assert cached.status_code == 201, cached.text  # a spotdown row is a hit: no new analysis
    menv.ops.sql("DELETE FROM swedebeat_mix_maps WHERE owner_id = :o", o=owner)
    refused = await create(browser, design_body(browser))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "analysis_limit"


async def test_a_refit_whose_row_is_gone_and_whose_audio_now_analyses_differently_is_music_changed(
    browsers: Any,
    menv: MixEnv,
    mowl_api: FakeMowl,
    spotdown: FakeSpotdown,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1353 (B1), #1357: the base's row is gone (30 days, the per-user cap; a ``local`` row can no
    longer exist), so the refit analyses again on spotdown, finds another map and ends
    ``music_changed``."""
    browser = await designed(browsers, menv, mowl_api, TOKEN_A)
    created = await create(browser, design_body(browser))
    await menv.run_next()
    [base] = revisions(menv, UUID(created.json()["id"]))
    menv.ops.sql("DELETE FROM swedebeat_mix_maps WHERE owner_id = :o", o=browser.user_id)
    # What spotdown now says of the same audio is another map.
    other = [(a, b, bpm + 10.0) for a, b, bpm in SONGS]
    monkeypatch.setattr(sys.modules[__name__], "SONGS", other)
    for song in base.music["songs"]:
        menv.ops.sql(
            "INSERT INTO beat_one_corrections (owner_id, class_id, fingerprint, song, beats, bpm) "
            "VALUES (:o, :c, :f, :s, 1, :b)",
            o=browser.user_id, c=base.class_id, f=base.music["fingerprint"], s=song["song"],
            b=song["bpm"],
        )  # fmt: skip
    fetches = len(menv.media.requests)
    made = await create(browser, ask(REFIT_KIND, base, browser))
    assert made.status_code == 201, made.text
    menv.configure()
    registry = KindRegistry()
    registry.register(REFIT_DRAFT)
    lease = menv.ops.app.settings.job_lease_seconds
    claimed = await queue.claim(menv.ops.factory, "sd-w", lease, [REFIT_KIND])
    assert claimed is not None
    await execute(
        Execution(claimed), menv.ops.factory, registry, RunConfig(lease_seconds=lease),
        asyncio.Event(),
    )  # fmt: skip
    refit = menv.ops.row(UUID(made.json()["id"]))
    assert (refit.state, refit.error_category) == ("failed", "music_changed")
    assert len(menv.media.requests) == fetches + 1  # analysed again: the row was gone
