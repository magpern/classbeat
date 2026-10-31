"""The job's Spotify request count, rate limits in a row and last hint (ADR 0009 section 8; #300)

Revision ID: 0013_spotify_quota
Revises: 0012_model_jobs
Create Date: 2026-10-31

Four columns on ``jobs``, all written by the worker in the job's fenced transactions:

- ``spotify_calls``: the Spotify requests the job has sent, across its stages and attempts
  (``CLASSBEAT_SPOTIFY_CALLS_PER_JOB``; the request over it is never sent, ``limit_exceeded``).
- ``spotify_rate_limits``: the attempts in a row that ended with a Spotify 429; the third ends
  the job ``quota_exhausted``. Any other ending of an attempt resets it; a MOWL 429 does not count.
- ``spotify_failure``: the category of the last attempt's failure when it came from Spotify and
  its sentence depends on that (``rate_limited``, ``upstream_unavailable``, ``quota_exhausted``),
  so the page shows Spotify's sentence and not the generic one; NULL otherwise.
- ``spotify_retry_after``: the last Spotify ``Retry-After`` (whole seconds, 1 to 86400), for
  "trying again in about N minutes" and "Try again in about N hours".
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_spotify_quota"
down_revision: str | Sequence[str] | None = "0012_model_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUOTA_RATE_LIMITS = 3  # classbeat.jobs.errors.QUOTA_RATE_LIMITS
MAX_RETRY_AFTER = 86_400  # classbeat.connections.errors.MAX_RETRY_AFTER

COLUMNS = (
    "ADD COLUMN spotify_calls integer NOT NULL DEFAULT 0 "
    "CONSTRAINT jobs_spotify_calls CHECK (spotify_calls >= 0)",
    "ADD COLUMN spotify_rate_limits smallint NOT NULL DEFAULT 0 "
    f"CONSTRAINT jobs_spotify_rate_limits CHECK (spotify_rate_limits BETWEEN 0 AND {QUOTA_RATE_LIMITS})",
    "ADD COLUMN spotify_failure text CONSTRAINT jobs_spotify_failure "
    "CHECK (spotify_failure IN ('rate_limited', 'upstream_unavailable', 'quota_exhausted'))",
    "ADD COLUMN spotify_retry_after integer CONSTRAINT jobs_spotify_retry_after "
    f"CHECK (spotify_retry_after BETWEEN 1 AND {MAX_RETRY_AFTER})",
)


def upgrade() -> None:
    op.execute("ALTER TABLE public.jobs " + ", ".join(COLUMNS))


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.jobs DROP COLUMN spotify_retry_after, DROP COLUMN spotify_failure, "
        "DROP COLUMN spotify_rate_limits, DROP COLUMN spotify_calls"
    )
