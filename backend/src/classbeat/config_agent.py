"""Agent-run and Spotify settings, a field group of ``classbeat.config.Settings``.

Pure: no I/O. The model API keys (secret files) and the Spotify ``localhost`` check stay on
``Settings`` in ``classbeat.config``. Never instantiated on its own.

A ``BaseSettings`` (not a plain ``BaseModel``) only so its ``model_config`` type matches
``Settings``'; the mixin adds no config of its own and declares no ``SecretStr`` field.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    """The model provider and the limits of agent runs (ADR 0006), song picking and the Spotify
    connection (ADR 0008, ADR 0009)."""

    # The model provider of the worker (ADR 0006 §9): ``anthropic`` (the default) needs the
    # ``anthropic_api_key`` secret file; ``fake`` (the dev stack and CI) is refused in ``prod``.
    # ``check_worker_model_provider`` enforces both at worker start.
    model_provider: Literal["anthropic", "fake"] = "anthropic"
    # Agent runs (ADR 0006 sections 2, 5 and 8; classbeat.agent). The model and its request.
    agent_model: str = Field(default="claude-opus-5-5", pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    agent_max_tokens: int = Field(default=32_000, ge=1_000, le=128_000)
    # Limits per stage run, and the cost ceiling per job across stages and attempts.
    agent_max_turns: int = Field(default=12, ge=1, le=100)
    agent_max_tool_calls: int = Field(default=40, ge=1, le=500)
    agent_max_corrections: int = Field(default=3, ge=0, le=20)
    agent_stage_seconds: float = Field(default=300.0, gt=0, le=3600)
    agent_job_cost_micros: int = Field(default=1_500_000, ge=0, le=100_000_000)  # $1.50
    # The song-picking kinds' own ceiling across their stages and attempts (ADR 0008 section 3).
    song_job_cost_micros: int = Field(default=3_000_000, ge=0, le=100_000_000)  # $3
    # Output tokens reserved per turn by the cost check (thinking included).
    agent_turn_output_reserve: int = Field(default=8_000, ge=0, le=128_000)
    # The FakeProvider's script (a JSON file) when model_provider is fake.
    fake_model_script: Path | None = None

    # Song picking and the Spotify connection (ADR 0008, ADR 0009). Off by default: the owner
    # switches live features on (roadmap Amendment 1); off, every Spotify route answers 404
    # ``feature_off``. A connect flow lives at most 10 minutes (the table's check), a token is
    # refreshed when it has less than the margin left, and a refresh request (inside the row
    # lock) ends within its timeout, below the role's 30 s statement and 60 s idle limits.
    song_picking: bool = False
    spotify_flow_ttl_seconds: int = Field(default=600, ge=60, le=600)
    spotify_refresh_margin_seconds: int = Field(default=120, ge=0, le=1800)
    spotify_refresh_timeout_seconds: float = Field(default=10.0, gt=0, le=20)
    # ADR 0008 section 2: how long a new Spotify playlist may take to appear in the user's
    # playlist listing. Unset (the default), an uncertain create whose listing shows no playlist
    # with its ``ref`` stays uncertain; set (by the owner, after measuring it at live activation),
    # such a create whose call returned at least this long ago is ``failed``. A day at most.
    spotify_listing_lag_seconds: float | None = Field(default=None, gt=0, le=86400)
    # ADR 0009 section 8: the Spotify requests one job may send across its stages and attempts
    # (the token refreshes included); the request over it is never sent (``limit_exceeded``).
    spotify_calls_per_job: int = Field(default=300, ge=1, le=10_000)
    # ADR 0008 section 2: how long a pick waits for MOWL's track list of the pool playlist to
    # show exactly the candidates just written (``pool_not_synced`` after it).
    spotify_pool_sync_wait_seconds: float = Field(default=120.0, ge=0, le=600)
