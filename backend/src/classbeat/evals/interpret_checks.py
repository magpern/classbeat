"""What an interpret case checks of a ``submit_brief`` (ADR 0029 section 4; pure).

On the sanitised ``InterpretedRequest`` (what the stage hands back), never on model text:

- ``values``: every expected field is set, to the expected closed value and, where the case says
  so, with the expected origin;
- ``not_values``: a field may be unset but never set to the given value (an injected one);
- ``absent``: the fields the request does not settle are left unset (the policy's defaults);
- ``games``: the games, in order, in the closed form the brief stores (type, placement, length);
- ``remove_games``: a revise that takes out every game says so (and reports no games);
- ``quotes``: every kept quote is a verbatim substring of the request;
- ``taste`` (#1140): the music taste's eras (in order, or the years they cover between them),
  language and hits, where the case says;
- ``pinned`` (#1185): the named songs, in order, with the expected title and, where the case
  says, artist and place (case aside);
- ``questions`` (#1185): every path the case ``asks`` is asked, and no question is asked of a
  field the reading states explicitly;
- ``name`` (#1233, #1237): a case that expects the class ``named`` got a short name, and any name
  that came back is one ``class_title_policy.proposed_name`` keeps as it is (clean, at most 60
  characters, no link); ``InterpretedRequest.sanitised`` drops one that is not, so this is the
  backstop;
- ``shape`` (#1485): the sections in order, each of the expected kind and with every modifier the
  case gives (``trend``, ``effort``, ``count``, ``length``, ``load``, ``minutes``);
- ``forbidden_text``: none of the case's strings is anywhere in what came back, the name included.
"""

import json
from collections.abc import Collection
from itertools import pairwise

from classbeat.agent.interpret_input import clean_request
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.ride_shape import SectionFields
from classbeat.class_title_policy import proposed_name
from classbeat.evals.checks import Check
from classbeat.evals.interpret_cases import InterpretCase, PinExpect, TasteExpect


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
    wrong += [p for p, v in expect.not_values.items() if p in read and read[p].value == v]
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
    if expect.remove_games:
        checks.append(
            Check("remove_games", got.remove_games is True, "removal of every game: said or not")
        )
    if expect.shape is not None:
        checks.append(_shape(expect.shape, got))
    if expect.taste is not None:
        checks.append(_taste(expect.taste, got))
    if expect.pinned is not None:
        checks.append(_pinned(expect.pinned, got))
    asked = [q.path for q in got.questions]
    stated = [p for p, r in read.items() if r.origin == "explicit" and p in asked]
    missing = [p for p in expect.asks if p not in asked]
    checks.append(
        Check(
            "questions",
            not missing and not stated,
            f"not asked: {', '.join(missing) or '-'}; asked of a stated field: "
            f"{', '.join(stated) or '-'}",
        )
    )
    if expect.named or got.name is not None:
        kept = got.name is not None and proposed_name(got.name) == got.name
        checks.append(Check("name", kept, "a short name that survives the cleaning: given or not"))
    text = json.dumps(got.model_dump(mode="json"), ensure_ascii=False).lower()
    found = [t for t in expect.forbidden_text if t.lower() in text]
    checks.append(Check("forbidden_text", not found, f"{len(found)} forbidden strings found"))
    return checks


def _taste(expect: TasteExpect, got: InterpretedRequest) -> Check:
    taste = got.music_taste
    eras = [[e.year_from, e.year_to] for e in taste.eras] if taste is not None else []
    language = taste.language.value if taste is not None and taste.language else None
    hits = taste is not None and taste.hits is not None
    wrong: list[str] = []
    if expect.eras is not None and eras != expect.eras:
        wrong.append("eras")
    if expect.span is not None and not _covers(eras, expect.span):
        wrong.append("span")
    if expect.language is not None and language != expect.language:
        wrong.append("language")
    if expect.hits is not None and hits != expect.hits:
        wrong.append("hits")
    return Check("taste", not wrong, f"wrong: {', '.join(wrong) or '-'}; eras {eras}")


def _shape(expect: list[SectionFields], got: InterpretedRequest) -> Check:
    made = [s.closed() for s in got.shape]
    wrong = [str(n) for n, (m, e) in enumerate(zip(made, expect, strict=False)) if not _holds(m, e)]
    if len(made) != len(expect):
        wrong.append("count")
    return Check(
        "shape", not wrong, f"{len(made)} sections of {len(expect)} expected; wrong: {wrong or '-'}"
    )


def _holds(made: SectionFields, want: SectionFields) -> bool:
    """The section is of the expected kind and has every modifier the case names."""
    given = want.model_dump(exclude_none=True)
    return all(made.model_dump().get(field) == value for field, value in given.items())


def _pinned(expect: list[PinExpect], got: InterpretedRequest) -> Check:
    def fold(text: str | None) -> str | None:
        return None if text is None else text.casefold()

    made = got.pinned_songs
    wrong = len(made) != len(expect) or any(
        fold(m.song) != fold(e.title)
        or (e.artist is not None and fold(m.artist) != fold(e.artist))
        or (e.at is not None and fold(m.at) != fold(e.at))
        for m, e in zip(made, expect, strict=False)
    )
    return Check("pinned", not wrong, f"{len(made)} pinned songs of {len(expect)} expected")


def _covers(eras: list[list[int]], span: list[int]) -> bool:
    """The eras, in order, run from ``span``'s first year to its last without a gap or overlap."""
    if not eras:
        return False
    joined = all(a[1] + 1 == b[0] for a, b in pairwise(eras))
    return joined and eras[0][0] == span[0] and eras[-1][1] == span[1]


def _set_of(absent: list[str], read: Collection[str]) -> str:
    return "set: " + (", ".join(sorted(set(absent) & set(read))) or "-")
