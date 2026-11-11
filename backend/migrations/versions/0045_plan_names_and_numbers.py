"""Plan names, numbers and gates for M7a (issue #947, ADR 0031 section 1)

Revision ID: 0045_plan_names_and_numbers
Revises: 0044_class_interpretation
Create Date: 2026-11-11

A data migration on the three seeded ``plans`` rows (ids, ranks, route keys and prices are not
touched, so eval results, entitlements and model routes keep pointing at the same rows):

| id           | name         | classes | revisions | jobs | minutes | games | FTP tests | races |
|--------------|--------------|---------|-----------|------|---------|-------|-----------|-------|
| free         | Free         | 3       | 3         | 1    | 30      | no    | no        | yes   |
| instructor   | Individual   | 9       | 10        | 2    | 75      | yes   | yes       | yes   |
| pro          | Professional | 30      | 20        | 3    | 180     | yes   | yes       | yes   |

The downgrade restores the values of migrations 0007 and 0026 (names Free, Instructor, Pro;
classes 3 / 30 / 100; revisions 3 / 10 / 25; jobs 1 / 2 / 3; minutes 30 / 60 / 90; games off on
Free only; races and FTP tests on everywhere). An edit an admin made to these columns is
replaced in both directions: the migration states the whole row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0045_plan_names_and_numbers"
down_revision: str | Sequence[str] | None = "0044_class_interpretation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (id, name, classes, revisions, jobs, minutes, games, ftp tests, races)
_COLUMNS = "id, name, classes, revisions, jobs, minutes, games, ftp, races"
NEW = """
    ('free', 'Free', 3, 3, 1, 30, false, false, true),
    ('instructor', 'Individual', 9, 10, 2, 75, true, true, true),
    ('pro', 'Professional', 30, 20, 3, 180, true, true, true)"""
OLD = """
    ('free', 'Free', 3, 3, 1, 30, false, true, true),
    ('instructor', 'Instructor', 30, 10, 2, 60, true, true, true),
    ('pro', 'Pro', 100, 25, 3, 90, true, true, true)"""


def _set(values: str) -> str:
    return f"""
    UPDATE public.plans SET name = v.name, classes_per_month = v.classes,
        revisions_per_class = v.revisions, concurrent_jobs = v.jobs,
        max_class_minutes = v.minutes, games_allowed = v.games,
        ftp_tests_allowed = v.ftp, races_allowed = v.races
    FROM (VALUES {values}) AS v ({_COLUMNS})
    WHERE plans.id = v.id"""


SEED = _set(NEW)  # ``tests/test_plans_m7_numbers.py`` applies it to a database seeded as 0007 did
RESTORE = _set(OLD)


def upgrade() -> None:
    op.execute(SEED)


def downgrade() -> None:
    op.execute(RESTORE)
