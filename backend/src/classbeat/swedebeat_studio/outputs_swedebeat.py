"""The SwedeBeat outputs of an approved studio draft (ADR 0036 section 6, SR C7).

**The playlist** (``playlist``): ``POST /playlists`` with the stored body (``output_body_policy``,
built when the attempt was opened), then ``POST /playlists/{id}/publish``, each an external
operation. The ``playlist_id`` is stored (``outputs_ops``) before the publish's write intent, so a
publish that failed resumes **on that stored id** and never creates a second playlist.

**The mix** (``mix``): with no merge operation yet (or only a ``failed`` one), and never before
the approval:

1. one of the worker's analysis slots (#574: the render is as heavy as an analysis);
2. each **distinct** track **downloaded fresh**, once per attempt however many pieces it plays
   in (ADR 0036 amendment A8; ``download_track``, 120 s and 60 MB each, counted in the daily
   track-download limit), into the render's private directory; one whose sha256 is not the
   analysed bytes' stops the output with ``music_changed`` ("the music changed, review again"),
   nothing sent. The render plan keeps one entry per piece, each naming its track's one file;
3. the render once (``studio.render.rendered_mix``, the sandboxed child) from the stored plan
   (``mowl.compose.mix_offsets``), its decoded length checked against the composed one and its
   file at most 400 MB, else ``analysis_failed`` and nothing sent;
4. the render's sha256 against the attempt's first (stored before the first write intent): a
   different one stops with ``render_not_reproducible``, its own message, nothing sent;
5. ``merge-publish`` with the exact bytes, streamed, under the stored unique name and
   ``X-Tracks``, through the upload client's own 900 s deadline; the URL is stored.

A merge operation that is ``pending``, ``uncertain`` or ``succeeded`` is never rendered or sent
again: the protocol reuses a success or ends the run ``uncertain_write`` (in doubt).
The audio is never kept: the directory goes with everything in it on every way out.
"""

import json
import logging
from pathlib import Path
from typing import Any, Final

from classbeat.design import tracklimit
from classbeat.egress_gateway import DOWNLOAD_SECONDS, MERGE_SECONDS
from classbeat.jobs import JobFailure
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.operations import CallResult, NotWritten
from classbeat.studio import render, render_policy
from classbeat.studio.output_key_policy import OutputKey, parse
from classbeat.swedebeat_gateway import client as gateway
from classbeat.swedebeat_gateway import opener, writes
from classbeat.swedebeat_gateway.write_policy import PUBLISH_BODY
from classbeat.swedebeat_match.gateway_match import call_failure
from classbeat.swedebeat_studio import outputs_ops, outputs_rows
from classbeat.swedebeat_studio.outputs_attempt import mark
from classbeat.swedebeat_studio.outputs_job import Producer, result_of
from classbeat.swedebeat_studio.outputs_rows import OutputRow, step_keys

logger = logging.getLogger(__name__)

OWN_CATEGORIES: Final = frozenset({"track_too_large", "track_unavailable", "internal"})


def _key(row: OutputRow, step: str) -> OutputKey:
    found = parse(step_keys(row)[step])
    if found is None:  # pragma: no cover - built by the same policy
        raise JobFailure("internal")
    return found


def _body(row: OutputRow) -> bytes:
    body = row.detail.get("body")
    if not isinstance(body, dict):
        raise JobFailure("internal")
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def playlist(p: Producer, row: OutputRow) -> None:
    """Create (or resume on the stored id) and publish the SwedeBeat playlist."""
    names = {"name": row.detail.get("name")}
    body = _body(row)
    playlist_id = row.detail.get("playlist_id")
    if not isinstance(playlist_id, str):
        create = outputs_ops.output_request(
            _key(row, "create"),
            p.draft.content_hash,
            {**names, "body": json.loads(body)},
            timeout_seconds=outputs_ops.CREATE_SECONDS,
            guard=p.still_approved(),
        )

        async def created() -> CallResult:
            async with opener.open_client(outputs_ops.CREATE_SECONDS) as client:
                made = await writes.create_playlist(client, p.key, p.bearer.bearer, body)
            return result_of(made)

        playlist_id = await p.write(create, created)
    publish = outputs_ops.output_request(
        _key(row, "publish"),
        p.draft.content_hash,
        {**names, "playlist_id": playlist_id, "body": json.loads(PUBLISH_BODY)},
        timeout_seconds=outputs_ops.PUBLISH_SECONDS,
        guard=p.still_approved(),
        # The reconciler lists with this connection and generation, or after a reconnect of that
        # connection to the same SwedeBeat account (``account``, a digest, #1117): ``outputs_ops``.
        # Context only, never in the hash: a reconnect must not change the request.
        context={
            "playlist_id": playlist_id,
            "connection_id": str(p.given.swedebeat_connection_id),
            "generation": p.given.swedebeat_generation,
            "account": p.bearer.account_ref,
        },
    )

    async def published() -> CallResult:
        async with opener.open_client(outputs_ops.PUBLISH_SECONDS) as client:
            await writes.publish_playlist(client, p.key, p.bearer.bearer, playlist_id)
        return result_of(playlist_id)

    await p.write(publish, published)


def _merge_fields(row: OutputRow) -> dict[str, Any]:
    detail = row.detail
    return {k: detail.get(k) for k in ("name", "plan", "sha256s", "x_tracks")}


async def _merge_state(p: Producer, row: OutputRow) -> str | None:
    async with p.run.fenced() as fs:
        steps = await outputs_rows.operations(fs.db, p.owner_id, row)
    found = steps.get("merge")
    return None if found is None else found[0]


async def _download(p: Producer, reference: str, dest: Path, analysed: str) -> None:
    async with p.run.fenced() as fs:
        await fs.extend_lease(DOWNLOAD_SECONDS + CALL_LEASE_MARGIN_SECONDS)
        await tracklimit.reserve(fs, 1)  # counted even if it fails
    await p.run.checkpoint()
    category, retry_after = "invalid_response", None
    try:
        async with opener.open_client(DOWNLOAD_SECONDS) as client:
            got = await gateway.download_track(client, p.key, p.bearer.bearer, reference, dest)
    except gateway.GatewayFailure as refused:
        category, retry_after = refused.category, refused.retry_after
    except ValueError:
        category = "internal"
    else:
        if got.sha256 != analysed:
            logger.info("studio mix track changed", extra={"job_id": str(p.run.lease.job_id)})
            raise JobFailure("music_changed")
        return
    if category in OWN_CATEGORIES:
        raise JobFailure(category)
    raise await call_failure(p.run, p.bearer, p.key, category, retry_after)


async def mix(p: Producer, row: OutputRow) -> None:
    """Render once and merge-publish the mix (see the module docstring)."""
    fields = _merge_fields(row)
    plan, sha256s = fields["plan"], fields["sha256s"]
    if not isinstance(plan, list) or not isinstance(sha256s, dict):
        raise JobFailure("internal")
    request = outputs_ops.output_request(
        _key(row, "merge"),
        p.draft.content_hash,
        fields,
        timeout_seconds=MERGE_SECONDS,
        guard=p.still_approved(),
    )
    if await _merge_state(p, row) not in (None, "failed"):

        async def never() -> CallResult:  # the protocol reuses or settles it; nothing is sent
            raise NotWritten("internal")

        await p.write(request, never)
        return
    distinct = list(dict.fromkeys(str(part["reference"]) for part in plan))
    async with p.run.fenced() as fs:
        await tracklimit.check(fs, len(distinct))
    p.run.require_analysis_slot()  # #574: requeued with one, the attempt not counted

    async def prepare(work: Path) -> list[dict[str, Any]]:
        files: dict[str, Path] = {}
        for n, reference in enumerate(distinct):  # each track once, its sha256 checked once
            files[reference] = work / f"track-{n:02d}.mp3"
            await _download(p, reference, files[reference], str(sha256s[reference]))
        tracks = [
            {
                "path": str(files[str(part["reference"])]),
                **{k: part[k] for k in render_policy.TRACK_INTS},
            }
            for part in plan
        ]
        await p.run.checkpoint()
        return tracks

    try:
        async with render.rendered_mix(prepare) as rendered:
            await _check_render(p, row, rendered.sha256)

            async def merged() -> CallResult:
                async with opener.open_upload_client() as client:
                    url = await writes.merge_publish(
                        client,
                        p.key,
                        p.bearer.bearer,
                        str(fields["name"]),
                        list(fields["x_tracks"]),
                        rendered.path,
                    )
                return result_of(outputs_ops.url_digest(url), url=url)

            await p.write(request, merged)
    except render.RenderFailed:
        raise JobFailure("analysis_failed") from None


async def _check_render(p: Producer, row: OutputRow, sha256: str) -> None:
    """The first render's sha256 is stored with the attempt; a later one must equal it."""
    async with p.run.fenced() as fs:
        await p.run.release_analysis_slot(fs)
        now = await outputs_rows.latest(
            fs.db, p.owner_id, row.class_id, row.revision_id, row.output, lock=True
        )
        first = None if now is None else now.detail.get("render_sha256")
        if first is None:
            await outputs_rows.update(fs.db, p.owner_id, row.id, patch={"render_sha256": sha256})
    p.run.slot_released()
    if first is not None and first != sha256:
        logger.warning(
            "studio mix render not reproducible", extra={"job_id": str(p.run.lease.job_id)}
        )
        await mark(p, row, "failed", category="render_not_reproducible")
        raise JobFailure("render_not_reproducible")
