"""An ``interpret`` case of the suite (ADR 0029 section 4): a free-text request, what the
interpreter may be told besides it, and what its ``submit_brief`` must say.

A file in ``suite/interpret/`` with ``"kind": "interpret"``. The case holds the request, the
sports the account may use, the plan's limits and the instructor's music names (as the stage is
given them); ``expect`` holds only closed values, never text: the brief path and its value, where
a value must have come from, the paths that must be left unset (the policy's defaults) and the
games, in order, in the closed form the brief stores. A request beyond the plan (a running class
with a game) still expects what was *asked*: the policy, not the interpreter, records "Not as
asked". A revise case (#904) also holds ``current``: the class's brief, of which the interpreter
is told the closed values (``interpret_input.current_values``), and expects only what the request
changes. A composer case (ADR 0038 section 3, #1185) may say how many playlists are
``attached`` and expect the ``pinned`` songs (title, artist and place, case aside, in order) and the
questions it ``asks`` (topics that must be among them; ``not_asks`` and ``no_questions`` the
reverse, #1536). The ride's ``shape`` (#1485) is judged
section by section, in order: a field the case gives must match, one it leaves out is not judged.
A new-class case may expect the class to be ``named`` (#1233): a short name that survives
``class_title_policy.proposed_name``. Pure: no I/O.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from classbeat.agent.brief import Brief, BriefGame, Sport
from classbeat.agent.interpret_input import MusicName
from classbeat.agent.interpreted import MAX_NAMES, SCALARS, StatedOrigin
from classbeat.agent.interpreted_composer import MAX_PINNED, MAX_QUESTIONS, QuestionTopic
from classbeat.agent.ride_shape import MAX_SECTIONS, SectionFields
from classbeat.design.interpret_composer_policy import TOPIC_PATHS

MAX_REQUEST: Final = 2000


class MusicEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["playlist", "mix"]
    name: str = Field(max_length=500)

    def named(self) -> MusicName:
        return MusicName(self.kind, self.name)


Years = list[int]  # [from, to]


class TasteExpect(BaseModel):
    """The music taste (#1140): the eras in order, or the years they must cover between them (a
    range longer than an era, split); the language; hits. ``None``: not judged."""

    model_config = ConfigDict(extra="forbid", strict=True)

    eras: list[Years] | None = None
    span: Years | None = Field(default=None, min_length=2, max_length=2)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    hits: bool | None = None

    @model_validator(mode="after")
    def _years(self) -> "TasteExpect":
        ranges = [*(self.eras or []), *([self.span] if self.span else [])]
        if any(len(r) != 2 or r[0] > r[1] for r in ranges):
            raise ValueError("a range is [from, to], the earlier year first")
        return self


class PinExpect(BaseModel):
    """A song the instructor named: its title, and its artist and place where the case says."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=100)
    artist: str | None = Field(default=None, min_length=1, max_length=100)
    at: str | None = Field(default=None, min_length=1, max_length=40)


class InterpretExpect(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    values: dict[str, JsonValue] = Field(default_factory=lambda: dict[str, JsonValue]())
    not_values: dict[str, JsonValue] = Field(
        default_factory=lambda: dict[str, JsonValue]()
    )  # a field may be unset, but never set to this (an injected value)
    origins: dict[str, StatedOrigin] = Field(default_factory=lambda: dict[str, StatedOrigin]())
    absent: list[str] = Field(default_factory=lambda: list[str]())
    games: list[BriefGame] | None = None  # in order; ``None``: the games are not judged
    remove_games: bool = False  # a revise that takes out every game says so (#908)
    taste: TasteExpect | None = None  # ``None``: the music taste is not judged (#1140)
    forbidden_text: list[str] = Field(default_factory=lambda: list[str]())  # case-insensitive
    pinned: list[PinExpect] | None = Field(default=None, max_length=MAX_PINNED)  # #1185
    # The topics that must be among the questions, the ones that must not, and "no questions at all"
    # (#1536): a topic is judged by what the model asked, never by its wording.
    asks: list[QuestionTopic] = Field(
        default_factory=lambda: list[QuestionTopic](), max_length=MAX_QUESTIONS
    )
    not_asks: list[QuestionTopic] = Field(
        default_factory=lambda: list[QuestionTopic](), max_length=MAX_QUESTIONS
    )
    no_questions: bool = False
    # The music question has no assumed answer and marks a "No music" option (#1536).
    music_required: bool = False
    named: bool = False  # a new class gets a short name that survives the cleaning (#1233)
    # The ride's sections in order (#1485); ``None``: not judged, ``[]``: the reading has none.
    shape: list[SectionFields] | None = Field(default=None, max_length=MAX_SECTIONS)

    @model_validator(mode="after")
    def _removal_leaves_no_games(self) -> "InterpretExpect":
        if self.remove_games and self.games:
            raise ValueError("a request that removes every game reports none")
        return self

    @model_validator(mode="after")
    def _paths(self) -> "InterpretExpect":
        known = set(SCALARS)
        named = set(self.values) | set(self.not_values) | set(self.origins) | set(self.absent)
        if not named <= known:
            raise ValueError(
                "expect names the scalar fields of a request only; games have their own"
            )
        stated = {p for p, o in self.origins.items() if o == "explicit"}
        if {TOPIC_PATHS[t] for t in self.asks if t in TOPIC_PATHS} & stated:
            raise ValueError("a question is never asked of a stated field")
        if self.music_required and "music" not in self.asks:
            raise ValueError("a required music question must also be in asks")
        if set(self.asks) & set(self.not_asks) or (self.no_questions and self.asks):
            raise ValueError("a topic is expected asked or not asked, not both")
        if set(self.values) & set(self.absent):
            raise ValueError("a field is expected set or absent, not both")
        if not set(self.origins) <= set(self.values):
            raise ValueError("an origin is expected of a value the case expects")
        return self


class InterpretCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["interpret"]
    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    about: str = Field(min_length=1, max_length=200)
    request: str = Field(min_length=1, max_length=MAX_REQUEST)
    sports: list[Sport] = Field(default_factory=lambda: list[Sport](["cycling"]), min_length=1)
    max_class_minutes: int | None = Field(default=None, ge=10, le=600)
    games_allowed: bool = True
    music: list[MusicEntry] = Field(
        default_factory=lambda: list[MusicEntry](), max_length=MAX_NAMES
    )
    current: Brief | None = None  # a revise: the class as it stands
    attached: int = Field(default=0, ge=0, le=5)  # the composer's attached playlists (#1185)
    expect: InterpretExpect

    @model_validator(mode="after")
    def _music_index(self) -> "InterpretCase":
        index = self.expect.values.get("music")
        if index is not None and not (isinstance(index, int) and 0 <= index < len(self.music)):
            raise ValueError("the expected music is an index into the case's music names")
        if self.expect.named and self.current is not None:
            raise ValueError("only a new class is named; a revise keeps its title")
        return self
