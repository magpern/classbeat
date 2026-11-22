"""One catalogue track downloaded fresh through the gateway's read-only ``download`` and checked
against the analysed bytes (ADR 0036 section 6, Mix; ADR 0042 section 6.5). Split out of
``outputs_swedebeat`` so the output mix and the audition's ``preview_render`` share it.

Before the request, in one fenced transaction: the lease extended over the download's own deadline
(``DOWNLOAD_SECONDS``, 120 s, and 60 MB in ``download_track``) and one row of the daily
track-download limit reserved (``tracklimit.reserve``: counted even if it fails). The file goes
into ``dest`` (a new file in a private directory). A file whose SHA-256 is not ``analysed`` ends
the job ``music_changed``; ``track_too_large``, ``track_unavailable`` and ``internal`` end it with
their own category, and anything else is the gateway's, through ``call_failure`` (a refused bearer
is confirmed with one session check). It makes **no external write**.
"""

import logging
from pathlib import Path
from typing import Final

from pydantic import SecretStr

from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.design import tracklimit
from classbeat.egress_gateway import DOWNLOAD_SECONDS
from classbeat.jobs import JobFailure, JobRun
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.swedebeat_gateway import client as gateway
from classbeat.swedebeat_gateway import opener
from classbeat.swedebeat_match.gateway_match import call_failure

logger = logging.getLogger(__name__)

OWN_CATEGORIES: Final = frozenset({"track_too_large", "track_unavailable", "internal"})


async def download_checked(
    run: JobRun,
    bearer: SwedebeatBearer,
    key: SecretStr,
    reference: str,
    dest: Path,
    analysed: str,
) -> None:
    """Download ``reference`` into ``dest`` and check it is the analysed bytes (see above)."""
    async with run.fenced() as fs:
        await fs.extend_lease(DOWNLOAD_SECONDS + CALL_LEASE_MARGIN_SECONDS)
        await tracklimit.reserve(fs, 1)  # counted even if it fails
    await run.checkpoint()
    category, retry_after = "invalid_response", None
    try:
        async with opener.open_client(DOWNLOAD_SECONDS) as client:
            got = await gateway.download_track(client, key, bearer.bearer, reference, dest)
    except gateway.GatewayFailure as refused:
        category, retry_after = refused.category, refused.retry_after
    except ValueError:
        category = "internal"
    else:
        if got.sha256 != analysed:
            logger.info("studio mix track changed", extra={"job_id": str(run.lease.job_id)})
            raise JobFailure("music_changed")
        return
    if category in OWN_CATEGORIES:
        raise JobFailure(category)
    raise await call_failure(run, bearer, key, category, retry_after)
