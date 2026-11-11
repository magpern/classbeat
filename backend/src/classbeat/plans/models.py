"""Response models shared by the plan routes."""

from pydantic import BaseModel


class LimitsOut(BaseModel):
    classes_per_month: int
    revisions_per_class: int
    concurrent_jobs: int
    max_class_minutes: int


class GatesOut(BaseModel):
    games: bool
    races: bool
    ftp_tests: bool
