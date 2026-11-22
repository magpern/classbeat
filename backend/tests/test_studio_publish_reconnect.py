"""#1117 (ADR 0036 section 6, ADR 0005 section 7): a SwedeBeat publish in doubt after the bearer
expired and the user reconnected, on the fakes (the gateway, Spotify), against Postgres as
``classbeat_app``; never the network.

- a reconnect of the approved connection to the **same** SwedeBeat account (its ``account_ref``
  equals the publish's stored ``account``) is read with the new bearer: listed settles
  ``succeeded`` (the output ``done``), not listed ``failed`` (safe to publish the same id again);
- a disconnect then a connect to another account (a new row, maybe at the approved generation)
  stays in doubt with ``connection_changed``, nothing read; the disconnect also removes the
  publish's recorded ``account``;
- another account, a publish approved before the account was on record, and a reconnect whose
  login named no account all stay in doubt with ``connection_changed``, nothing read;
- the reason is the check's own (``check_reason``): the job's ``category`` the user was shown
  stays, and a later check that settles the step clears the reason.
"""

from typing import Any

import pytest
from pydantic import SecretStr

from classbeat.connections.repository import disconnect
from classbeat.connections.swedebeat_account import connect_swedebeat
from ctx import make_context
from gatewaycore import FakeGateway, Reply, Session
from gatewayfake import CLIENT_KEY, EMAIL, EMAIL_B, PASSWORD, PASSWORD_B
from studiooutputs import act, operations, outputs_on, produce, sent, view  # noqa: F401
from studiotools import StudioEnv, StudioRider
from test_studio_produce_playlist import SHARED, Publish, on_publish, published
from test_swedebeat_studio_design import (  # pyright: ignore[reportPrivateUsage]
    WHOLE_8,
    analysed,  # noqa: F401 - the autouse stand-in of the sandboxed child
    designed,
    spotdown,  # noqa: F401 - the fake spotdown that stand-in answers from
)

pytestmark = pytest.mark.usefixtures("analysis_slot_by_default", "outputs_on")
DEVICE_SECRET = b"test-device-secret-1117"


async def connect(
    studio: StudioEnv, rider: StudioRider, email: str = EMAIL, password: str = PASSWORD
) -> None:
    """A connect or reconnect through the fake gateway's login, as the route makes it."""
    await connect_swedebeat(
        studio.songs.factory,
        studio.songs.ring,
        make_context(rider.user_id),
        email,
        password,
        force=True,
        device_secret=DEVICE_SECRET,
        key=SecretStr(CLIENT_KEY),
    )


async def in_doubt(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, *, takes_effect: bool, connected: bool
) -> tuple[StudioRider, Any, Any]:
    """A designed class whose publish is in doubt (a 502 after the request, the job's own check
    reading nothing). ``takes_effect``: the gateway published it anyway. ``connected``: the
    approval ran after a real connect (its account on record), else on a row without one."""
    rider, class_id, _, current = await designed(studio, WHOLE_8)
    fake = studio.gateway
    if connected:
        await connect(studio, rider)

    def lost(real: Publish, pid: str, body: Any, session: Session) -> Reply:
        if takes_effect:
            real(pid, body, session)
        fake.inject("status", SHARED, status=502)  # the job's own check reads nothing
        return Reply(502, b'{"detail": "SwedeBeat failed"}')

    on_publish(monkeypatch, fake, lost)
    done = await produce(studio, rider, class_id, current, "swedebeat_playlist")
    assert (done.state, done.error_category) == ("failed", "uncertain_write")
    shown = (await view(rider, class_id))["swedebeat_playlist"]
    assert (shown["state"], shown["category"]) == ("in_doubt", "uncertain_write")
    return rider, class_id, current


async def check_again(rider: StudioRider, class_id: Any, current: Any) -> dict[str, Any]:
    body = {"revision_id": str(current.current_revision_id)}
    checked = await act(rider, class_id, "swedebeat_playlist", "check-again", body)
    assert checked.status_code == 200, checked.text
    return checked.json()


def publish_state(studio: StudioEnv, rider: StudioRider) -> str:
    (publish,) = [
        o for o in operations(studio, rider.user_id) if o.kind == "swedebeat_publish_playlist"
    ]
    return publish.state


def expire_all(fake: FakeGateway) -> None:
    for bearer in list(fake.bearers):
        fake.expire(bearer)


@pytest.mark.parametrize("listed", [True, False])
async def test_a_reconnect_to_the_same_account_settles_the_publish(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, listed: bool
) -> None:
    rider, class_id, current = await in_doubt(
        studio, monkeypatch, takes_effect=listed, connected=True
    )
    fake = studio.gateway
    before = studio.songs.sql(
        "SELECT generation FROM connections WHERE id = :c", c=rider.swedebeat_connection_id
    ).scalar_one()
    expire_all(fake)  # the bearer of the approval is gone
    await connect(studio, rider)  # the same SwedeBeat account, a new generation
    after = studio.songs.sql(
        "SELECT generation FROM connections WHERE id = :c", c=rider.swedebeat_connection_id
    ).scalar_one()
    assert after == before + 1
    reads = len(sent(fake, "GET", SHARED))
    shown = await check_again(rider, class_id, current)
    assert len(sent(fake, "GET", SHARED)) > reads  # read with the new bearer
    assert "check_reason" not in shown
    if listed:
        assert (shown["state"], publish_state(studio, rider)) == ("done", "succeeded")
    else:  # every page read, not listed: publishing the same id again is safe
        assert (shown["state"], publish_state(studio, rider)) == ("failed", "failed")
    assert len(sent(fake, "POST", "publish")) == 1  # Check again never publishes


async def test_a_reconnect_to_another_account_stays_in_doubt_and_keeps_the_category(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    rider, class_id, current = await in_doubt(
        studio, monkeypatch, takes_effect=False, connected=True
    )
    fake = studio.gateway
    await connect(studio, rider, EMAIL_B, PASSWORD_B)  # the same row, another SwedeBeat account
    reads = len(sent(fake, "GET", SHARED))
    shown = await check_again(rider, class_id, current)
    assert (shown["state"], shown["check_reason"]) == ("in_doubt", "connection_changed")
    assert shown["category"] == "uncertain_write"  # what the user was shown stays
    assert publish_state(studio, rider) == "uncertain"  # never "not listed" for another account
    assert len(sent(fake, "GET", SHARED)) == reads  # nothing read under the other bearer
    # Back on the approved account: the next check settles it and clears the reason.
    await connect(studio, rider)
    shown = await check_again(rider, class_id, current)
    assert (shown["state"], publish_state(studio, rider)) == ("failed", "failed")
    assert "check_reason" not in shown
    assert published(fake) == []


@pytest.mark.parametrize("missing", ["approval", "row", "both"])
async def test_a_reconnect_without_the_account_on_record_stays_in_doubt(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """``approval``: approved on a row from before #1117 (no ``account`` in the publish);
    ``row``: the reconnect's login named no account; ``both``: neither has one (never equal)."""
    rider, class_id, current = await in_doubt(
        studio, monkeypatch, takes_effect=True, connected=missing == "row"
    )
    fake = studio.gateway
    fake.answers_customer = missing == "approval"
    await connect(studio, rider)  # the same email: maybe the same account, not provably
    ref = studio.songs.sql(
        "SELECT account_ref FROM connections WHERE id = :c", c=rider.swedebeat_connection_id
    ).scalar_one()
    assert (ref is None) == (missing != "approval")
    reads = len(sent(fake, "GET", SHARED))
    shown = await check_again(rider, class_id, current)
    assert (shown["state"], shown["check_reason"]) == ("in_doubt", "connection_changed")
    assert shown["category"] == "uncertain_write"
    assert publish_state(studio, rider) == "uncertain"
    assert len(sent(fake, "GET", SHARED)) == reads


@pytest.mark.parametrize("connected", [False, True])
async def test_a_disconnect_then_another_account_stays_in_doubt(
    studio: StudioEnv, monkeypatch: pytest.MonkeyPatch, connected: bool
) -> None:
    """The new row has another id. Without an account on record (``connected=False``) it is at
    the approved generation 1 too, so only the row-id check keeps the other account's bearer
    from listing it."""
    rider, class_id, current = await in_doubt(
        studio, monkeypatch, takes_effect=False, connected=connected
    )
    fake = studio.gateway
    approved = rider.swedebeat_connection_id
    context = (
        "SELECT request_context FROM external_operations "
        "WHERE owner_id = :o AND kind = 'swedebeat_publish_playlist'"
    )
    had = studio.songs.sql(context, o=rider.user_id).scalar_one()
    assert ("account" in had and had["account"] is not None) == connected
    assert await disconnect(studio.songs.factory, make_context(rider.user_id), "swedebeat")
    assert "account" not in studio.songs.sql(context, o=rider.user_id).scalar_one()
    await connect(studio, rider, EMAIL_B, PASSWORD_B)  # a new row for another account
    row = studio.songs.sql(
        "SELECT id, generation FROM connections WHERE owner_id = :o AND provider = 'swedebeat'",
        o=rider.user_id,
    ).one()
    assert row.id != approved
    assert row.generation == had["generation"] or connected
    reads = len(sent(fake, "GET", SHARED))
    shown = await check_again(rider, class_id, current)
    assert (shown["state"], shown["check_reason"]) == ("in_doubt", "connection_changed")
    assert shown["category"] == "uncertain_write"
    assert publish_state(studio, rider) == "uncertain"
    assert len(sent(fake, "GET", SHARED)) == reads  # never listed under the other bearer
    assert published(fake) == []
