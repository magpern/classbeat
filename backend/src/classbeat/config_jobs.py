"""Worker, job and usage-limit settings, a field group of ``classbeat.config.Settings``.

Pure: no I/O. Never instantiated on its own: ``Settings`` inherits these fields and validators.

A ``BaseSettings`` (not a plain ``BaseModel``) only so its ``model_config`` type matches
``Settings``'; the mixin adds no config of its own and declares no ``SecretStr`` field.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class JobSettings(BaseSettings):
    """The worker's timing, the job queue (ADR 0005) and the per-user and per-plan limits."""

    # The worker's claim poll when no ``jobs_queued`` notification arrives (ADR 0005 section 3).
    worker_poll_seconds: float = 2.0
    # How often the worker purges expired auth rows (ADR 0003 section 1: hourly).
    purge_interval_seconds: float = 3600.0

    # Jobs (ADR 0005 sections 3 and 4).
    job_lease_seconds: int = Field(default=120, ge=30, le=600)
    # The worker's engine pool follows it (classbeat.worker.loop.worker_pool: 3 * n + 2 at most).
    worker_concurrency: int = Field(default=2, ge=1, le=16)
    # Running jobs that may analyse a Swedebeat mix at once, across every worker (#574): a job
    # that must analyse waits queued for one of these slots, so analyses never fill every worker
    # slot. queue_claim enforces it; each worker passes its own value. Keep it below
    # worker_concurrency summed over all workers: at or above it, analyses can take every slot
    # and delay design jobs, which is what the cap is for.
    mix_analysis_concurrency: int = Field(default=1, ge=1, le=16)
    # Reap, expire and settle (ADR 0005 section 3: every 30 s).
    worker_maintenance_seconds: float = Field(default=30.0, gt=0)
    # On SIGTERM, how long runs may take to reach a checkpoint or return from a call before
    # their leases are handed back (inside compose's 30 s stop_grace_period).
    worker_shutdown_wait_seconds: float = Field(default=25.0, ge=0, le=25)
    approval_ttl_hours: int = Field(default=168, ge=1, le=8760)
    # operation_busy requeues per phase that cost no attempt; after that each one counts (#86).
    job_busy_requeue_limit: int = Field(default=5, ge=0, le=100)
    # Queued and running jobs per user; creation and approval above it are 429 (ADR 0005 s. 2).
    jobs_per_user: int = Field(default=3, ge=1, le=100)
    # Revising a class by chat (ADR 0007 section 4): chat revisions a class may have (the first
    # design is not counted), user requests stored per class, and how many earlier requests
    # along the revision chain go back to the model.
    revisions_per_class: int = Field(default=25, ge=1, le=200)
    # ADR 0011 section 4: the environment's upper bound on a trial's length. A stored
    # ``trial.days`` above it is clamped when a trial starts; 0 turns trials off.
    trial_days_max: int = Field(default=30, ge=0, le=366)
    # ADR 0011 section 4: the environment's upper bounds on every plan's money limits; a plan's
    # effective ``classes_per_month`` and ``monthly_cost_micros`` are the smaller of the row
    # and these. ``CLASSBEAT_JOBS_PER_USER`` and ``CLASSBEAT_REVISIONS_PER_CLASS`` bound the
    # plan's concurrent jobs and revisions per class alike.
    classes_per_month_max: int = Field(default=200, ge=0, le=10_000)
    monthly_cost_micros_max: int = Field(default=300_000_000, ge=0, le=100_000_000_000)
    # Issue #496: the environment's upper bound on every plan's ``max_class_minutes`` (the
    # brief's own range, 10 to 180); a plan's effective cap is the smaller of the row and this.
    class_minutes_max: int = Field(default=180, ge=10, le=180)
    # Issue #577: Save and Update (``save_workout`` and ``update_workout`` jobs together) per user
    # in the last hour and the last 24 hours. Neither takes a credit or a model call, so without
    # these a script with a stolen session could keep writing programs to the user's MOWL.
    saves_per_hour: int = Field(default=30, ge=1, le=1_000)
    saves_per_day: int = Field(default=100, ge=1, le=10_000)
    # Issue #577: the general limit on signed-in ``/api`` requests (ADR 0003 section 9, amendment
    # of 2026-11-05), fixed windows per user (all requests, per minute and per hour; writes and
    # the class and job reads per minute) and per client address (signed-in requests, per minute).
    api_requests_per_minute: int = Field(default=300, ge=1, le=100_000)
    api_requests_per_hour: int = Field(default=3_000, ge=1, le=1_000_000)
    api_writes_per_minute: int = Field(default=60, ge=1, le=100_000)
    api_reads_per_minute: int = Field(default=120, ge=1, le=100_000)
    api_ip_requests_per_minute: int = Field(default=1_200, ge=1, le=1_000_000)
    # Issue #632: how many days the hourly purge keeps ``security_log`` rows (one per signed-in
    # ``/api`` request; ADR 0003 amendment "security log"). Short by design, at most the 30 days
    # of ``auth_events``.
    security_log_days: int = Field(default=7, ge=1, le=30)
    chat_max_requests: int = Field(default=50, ge=1, le=200)
    chat_context_requests: int = Field(default=10, ge=1, le=30)
    # ADR 0007 section 8: how long a new MOWL workout may take to appear in My Workouts. Unset
    # (the default), an uncertain save that left no new program stays uncertain for the user;
    # set (by the owner, after measuring it at live activation), such a save whose call returned
    # at least this long ago is ``failed``. A day at most.
    mowl_listing_lag_seconds: float | None = Field(default=None, gt=0, le=86400)

    @field_validator("worker_poll_seconds")
    @classmethod
    def _check_poll(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("worker_poll_seconds must be greater than 0")
        return value

    @field_validator("purge_interval_seconds")
    @classmethod
    def _check_purge_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("purge_interval_seconds must be greater than 0")
        return value
