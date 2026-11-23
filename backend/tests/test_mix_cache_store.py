"""``mix_cache.store``'s own refusal (#1299 review): a mix map without its provenance is
``internal`` before the run or the database is touched, as ``track_cache.store`` does, since
migration 0081 made ``swedebeat_mix_maps.provenance`` required. Pure: no database."""

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from classbeat.design import mix_cache
from classbeat.jobs import JobFailure, JobRun


async def test_the_mix_store_refuses_a_map_without_its_provenance() -> None:
    analysis = mix_cache.MixAnalysis(
        "mix-1", 1, "A mix", 60.0, 60.0, {"songs": []}, "0" * 64, cached=False, provenance=None
    )
    run = cast(JobRun, SimpleNamespace())  # no fenced(): any use of the run fails the test
    with pytest.raises(JobFailure) as refused:
        await mix_cache.store(run, cast(Any, None), analysis, uuid4(), mix_cache.Timing(0, 0))
    assert refused.value.args[0] == "internal"
