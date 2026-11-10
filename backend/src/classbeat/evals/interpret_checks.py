"""What an interpret case checks of a ``submit_brief`` (ADR 0029 section 4; pure).

On the sanitised ``InterpretedRequest`` (what the stage hands back), never on model text:

- ``values``: every expected field is set, to the expected closed value and, where the case says
  so, with the expected origin;
- ``absent``: the fields the request does not settle are left unset (the policy's defaults);
- ``games``: the games, in order, in the closed form the brief stores (type, placement, length);
- ``quotes``: every kept quote is a verbatim substring of the request;
- ``forbidden_text``: none of the case's strings is anywhere in what came back.
"""

import json
from collections.abc import Collection

from classbeat.agent.interpret_input import clean_request
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.evals.checks import Check
from classbeat.evals.interpret_cases import InterpretCase


def check_interpretation(case: InterpretCase, got: InterpretedRequest) -> list[Check]:
    read = {r.path: r for r in got.readings()}
    expect = case.expect
    wrong = [
        path
        for path, value in expect.values.items()
        if path not in read
        or read[path].value != value
        or (path in expect.origins and read[path].origin != expect.origins[path])
    ]
    request = clean_request(case.request)
    quotes = [r.path for r in read.values() if r.quote is not None and r.quote not in request]
    checks = [
        Check("values", not wrong, f"wrong or missing: {', '.join(wrong) or '-'}"),
        Check("absent", not (set(expect.absent) & set(read)), _set_of(expect.absent, read)),
        Check("quotes", not quotes, f"not in the request: {', '.join(quotes) or '-'}"),
    ]
    if expect.games is not None:
        asked = [g.model_dump(mode="json", exclude_none=True) for g in expect.games]
        made = [g.brief_game().model_dump(mode="json", exclude_none=True) for g in got.games]
        checks.append(Check("games", asked == made, f"{len(made)} games of {len(asked)} expected"))
    text = json.dumps(got.model_dump(mode="json"), ensure_ascii=False).lower()
    found = [t for t in expect.forbidden_text if t.lower() in text]
    checks.append(Check("forbidden_text", not found, f"{len(found)} forbidden strings found"))
    return checks


def _set_of(absent: list[str], read: Collection[str]) -> str:
    return "set: " + (", ".join(sorted(set(absent) & set(read))) or "-")
