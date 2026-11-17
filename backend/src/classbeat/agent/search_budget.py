"""The Spotify searches one ``pick_pool`` stage may send (issue #1140).

``search_tracks`` sends one search per call and ``find_songs`` one per song (up to 10 per call), so
without a cap one stage of 30 tool calls could send about 290 searches against the job's whole
``CLASSBEAT_SPOTIFY_CALLS_PER_JOB`` (300 by default, ``config_agent``), and the job would then fail
``limit_exceeded`` in the pool playlist write. The stage therefore holds ``MAX_STAGE_SEARCHES``: a
call that would go over it sends nothing and is answered ``limit_exceeded`` with how many searches
are left, so the model can send fewer songs or build the pool from what it has. Three attempts of a
job at the cap still leave room for the write (``tests/test_find_songs.py``).
"""

from typing import Final

from classbeat.agent.stage import ToolError

MAX_STAGE_SEARCHES: Final = 60


class SearchBudget:
    """The searches left in one stage; ``take`` reserves them before any is sent."""

    def __init__(self, searches: int = MAX_STAGE_SEARCHES) -> None:
        self.left = searches

    def __repr__(self) -> str:
        return f"SearchBudget({self.left} left)"

    def take(self, searches: int) -> None:
        if searches <= self.left:
            self.left -= searches
            return
        if self.left == 0:
            message = "no Spotify searches are left in this step; build the pool from what you have"
        else:
            message = (
                f"only {self.left} Spotify searches are left in this step; "
                f"send at most {self.left} songs, or build the pool from what you have"
            )
        raise ToolError("limit_exceeded", [message])
