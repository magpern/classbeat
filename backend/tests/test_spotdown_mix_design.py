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
from typing import Any
from uuid import UUID

import pytest

from classbeat.design.refit import KIND as REFIT_KIND
from classbeat.design.refit import REFIT_DRAFT
from classbeat.design.revise import KIND as REVISE_KIND
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.spotdown import mix_config
from classbeat.spotdown.studio_tracks_config import TracksConfig
from mowlfake import FakeMowl
from optools import TOKEN_A, TOKEN_B
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

pytestmark = pytest.mark.usefixtures("jenv", "mowl_api", "analysis_slot_by_default")
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

    monkeypatch.setattr(spotdown, "analysis", analysis)
    yield
    mix_config.configure(None)


def turn_on(owner: UUID) -> None:
    mix_config.configure(TracksConfig("allowlist", frozenset({owner}), frozenset({"upload"}), KEY))


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


async def test_a_class_designed_on_spotdowns_map_keeps_it_when_switched_off(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl, spotdown: FakeSpotdown
) -> None:
    browser = await designed(browsers, menv, mowl_api, TOKEN_A)
    turn_on(browser.user_id)
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

    # Switched off (the rollback lever): the revise reads the row the class was designed on.
    mix_config.configure(None)
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


async def test_with_spotdown_failing_the_design_is_todays(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl, spotdown: FakeSpotdown
) -> None:
    off_browser = await designed(browsers, menv, mowl_api, TOKEN_A)
    off_created = await create(off_browser, design_body(off_browser))
    await menv.run_next()
    [off] = revisions(menv, UUID(off_created.json()["id"]))

    spotdown.end = "processing_failed:decode_error"
    browser = await designed(browsers, menv, mowl_api, TOKEN_B)
    turn_on(browser.user_id)
    created = await create(browser, design_body(browser))
    await menv.run_next()
    [on] = revisions(menv, UUID(created.json()["id"]))
    assert spotdown.seen  # spotdown was asked, and failed
    assert bytes(on.content_hash) == bytes(off.content_hash)
    assert on.content == off.content
    drop = {"link_id"}
    assert {k: v for k, v in on.music.items() if k not in drop} == {
        k: v for k, v in off.music.items() if k not in drop
    }
    assert "analyser" not in on.music
    assert "provenance" not in on.music


async def test_admit_counts_only_a_local_row_as_a_cached_mix(
    browsers: Any, menv: MixEnv, mowl_api: FakeMowl
) -> None:
    """The web app cannot know whether spotdown is a worker's source for this owner, so a
    ``spotdown`` row alone never skips the daily limit (stricter than a hit)."""
    from classbeat.design import mixlimit
    from test_swedebeat_design import plant_analyses

    browser = await designed(browsers, menv, mowl_api, TOKEN_A)
    turn_on(browser.user_id)
    created = await create(browser, design_body(browser))
    await menv.run_next()
    assert menv.ops.row(UUID(created.json()["id"])).state == "completed"
    owner = browser.user_id
    plant_analyses(menv, owner, mixlimit.ANALYSES_PER_DAY - 1)
    assert menv.ops.sql(
        "SELECT analyser FROM swedebeat_mix_maps WHERE owner_id = :o", o=owner
    ).scalars().all() == ["spotdown"]
    refused = await create(browser, design_body(browser))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["reason"] == "analysis_limit"
    menv.ops.sql(
        "UPDATE swedebeat_mix_maps SET analyser = 'local', provenance = NULL WHERE owner_id = :o",
        o=owner,
    )
    cached = await create(browser, design_body(browser))
    assert cached.status_code == 201, cached.text
