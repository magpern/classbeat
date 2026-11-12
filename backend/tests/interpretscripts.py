"""Recorded ``FakeProvider`` scripts for the suite's interpret cases (ADR 0029 section 4): the
submission a good model would make, built from the case's own expectations, so a case and its
script cannot drift apart. Closed values only; the one quote is cut from the request."""

from typing import Any

from classbeat.agent.interpret import FINAL_TOOL
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.evals.interpret_cases import InterpretCase


def submission(case: InterpretCase) -> dict[str, Any]:
    e = case.expect
    out: dict[str, Any] = {}
    for path, value in e.values.items():
        item: dict[str, Any] = {"value": value, "origin": e.origins.get(path, "explicit")}
        out[path] = item
    first = case.request.split()[0]  # a verbatim word of the request
    if out:
        next(iter(out.values()))["quote"] = first
    games = []
    for game in e.games or []:
        item = game.model_dump(mode="json", exclude_none=True)
        item["origin"] = "explicit"
        games.append(item)
    if games:
        out["games"] = games
    if e.remove_games:
        out["remove_games"] = True
    return out


def script_for(case: InterpretCase) -> FakeScript:
    call = {"name": FINAL_TOOL, "input": submission(case), "expect": {"accepted": True}}
    return FakeScript.model_validate({"model": "fake-model", "turns": [{"calls": [call]}]})


def provider_for_case(case: InterpretCase) -> FakeProvider:
    return FakeProvider(script_for(case))
