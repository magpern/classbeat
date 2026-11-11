"""The Advanced form's values count as explicit (ADR 0029 section 4, plan "Done when" 6, #924).

The body below is what the form posts for ``design_draft`` (``formModel.ts``: every value it
submits, with the ``explicit`` paths it adds; ``formModel.test.ts`` pins the same paths). It goes
through the kind's input model as the jobs API validates it and then through the worker, so
nothing here sets ``explicit`` by hand on a ``Brief``. The fake model's first draft has one
climb: a form that asked for five is held to it, and the miss is a Not as asked item.
"""

import json
from typing import Any

import pytest

from classbeat import design
from classbeat.design import kind as design_kind
from designtools import DesignEnv, FakeMowlMusic
from jobtools import new_job
from optools import TOKEN_A

pytestmark = pytest.mark.usefixtures("jenv")

FORM_BRIEF: dict[str, Any] = {
    "length_min": 30,
    "climbs": 5,
    "sprints": 2,
    "races": 0,
    "intensity": "moderate",
    "ftp_test": None,
    "style": "",
    "text": "",
    "explicit": ["length_min", "climbs", "sprints"],  # a count of 0 states nothing
}


async def test_a_class_designed_from_the_form_reports_what_it_missed(
    denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    denv.script = "form_climbs_missed"  # three refused submissions, then the draft is kept
    owner = await denv.ops.user()
    conn = await denv.ops.connect(owner, TOKEN_A)
    body = {**denv.input(conn), "brief": FORM_BRIEF}
    given = design_kind.DesignDraftInput.model_validate(body)  # as ``POST /api/jobs`` does
    assert given.brief.explicit == FORM_BRIEF["explicit"]
    job_id = new_job(denv.admin, owner, kind=design.KIND, input=json.dumps(body))
    await denv.run_next()

    assert denv.ops.row(job_id).state == "completed"
    [revision] = denv.revisions(job_id)
    items = {i["path"]: i for i in revision.provenance["interpretation"]}
    assert (items["climbs"]["origin"], items["climbs"]["reason_code"]) == (
        "not_as_asked",
        "count_off",
    )
    assert items["length_min"]["origin"] == "explicit"
    # The form's own value reached the model as a correction to keep.
    [provider] = denv.providers
    assert "The instructor asked for 5 climbs." in repr(provider.sent)
