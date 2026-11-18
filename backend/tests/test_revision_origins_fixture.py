"""``fixtures/revision_origins.json`` is every revision origin the server classifies
(``revision_ready_policy``: ready or not ready, which ``test_revision_ready_guard.py`` holds equal
to every origin literal in the source) written out. The frontend's ``revisionOrigins.test.ts``
holds its origin labels and history words to the same file, so a new origin fails there until
the page classifies it (#1186). Pure."""

import json
from pathlib import Path

from classbeat.design.revision_ready_policy import NOT_READY_ORIGINS, READY_ORIGINS

FIXTURE = Path(__file__).parent / "fixtures" / "revision_origins.json"


def test_the_frontend_fixture_lists_every_classified_origin() -> None:
    classified = {o for o in READY_ORIGINS | NOT_READY_ORIGINS if o is not None}
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == sorted(classified)
