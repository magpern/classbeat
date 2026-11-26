"""A pool write's own MOWL attach is never the user's to resolve (#1546; ADR 0006 amendment of
2026-11-26, ``classbeat.operations.user``): an ``uncertain`` attach under a per-write key
(``spotify:<id>:<w16>``) is listed nowhere and not found by id, so Check again and Do it anyway
answer 404 ``not_found`` and leave it as it is, like the pool's create and replace rows. No later
job shares its key (Do it anyway would never be taken over) and the next pick attaches under its
own. A plain ``spotify:<id>`` attach (the final playlist, a first link) stays offered.

Fake MOWL only (``optools``), never the network.
"""

from typing import Any

from optools import PLAYLIST, FakeMowlAttach, OpsEnv
from writtentools import Written

OPS = "/api/external-operations"
NOT_FOUND = (404, {"detail": {"reason": "not_found"}})


async def test_an_uncertain_per_write_attach_is_hidden_and_a_plain_one_is_offered(
    xenv: OpsEnv, fake_attach: FakeMowlAttach, make_actor: Any
) -> None:
    owner = await xenv.user()
    connection = await xenv.connect(owner)
    written = Written(xenv)
    fake_attach.mode = "timeout_after_accept"  # MOWL may have attached: uncertain
    per_write_job = written.job(owner, connection, "job-1:pool-replace")
    row = await written.run(per_write_job)
    assert (row.state, row.error_category) == ("failed", "uncertain_write")
    plain_job = xenv.job(owner, connection)
    await xenv.run_next()
    assert xenv.row(plain_job).error_category == "uncertain_write"
    fake_attach.mode = "ok"
    hidden, plain = written.attaches(owner)
    assert hidden.idempotency_key.startswith(f"spotify:{PLAYLIST}:")
    assert len(hidden.idempotency_key) == len(f"spotify:{PLAYLIST}:") + 16
    assert plain.idempotency_key == f"spotify:{PLAYLIST}"
    assert (hidden.state, plain.state) == ("uncertain", "uncertain")

    actor = await make_actor(owner)
    listed = (await actor.get(OPS)).json()["operations"]
    assert [o["id"] for o in listed] == [str(plain.id)]
    assert (await actor.get(f"{OPS}?job_id={per_write_job}")).json() == {"operations": []}
    assert (await actor.get(f"{OPS}/{hidden.id}")).status_code == 404
    for action in ("check-again", "do-it-anyway"):
        answer = await actor.post(f"{OPS}/{hidden.id}/{action}")
        assert (answer.status_code, answer.json()) == NOT_FOUND
    [same, _] = written.attaches(owner)
    assert (same.state, same.resolved_by) == ("uncertain", None)

    # The plain attach is the user's: found, checked (inconclusive), then decided.
    assert (await actor.get(f"{OPS}/{plain.id}")).json()["state"] == "uncertain"
    checked = await actor.post(f"{OPS}/{plain.id}/check-again")
    assert (checked.status_code, checked.json()["state"]) == (200, "uncertain")
    decided = await actor.post(f"{OPS}/{plain.id}/do-it-anyway")
    assert (decided.status_code, decided.json()["state"]) == (200, "failed")
    assert len(fake_attach.attaches) == 2  # no user action sent anything
