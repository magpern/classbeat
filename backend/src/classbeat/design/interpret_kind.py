"""The ``interpret_request`` job kind (ADR 0029 section 5, MA1b-3): one free-text request in, the
brief, the music and the interpretation items out, stored on the class it creates. No design.

Input: ``{request, music_override?, music_sources?, sport_override?, mowl_connection_id,
spotify_connection_id?, title?}`` (``InterpretRequestInput``; ``music_sources`` is the
composer's attached music, ``design.music_sources``). The request is 1 to 2000 visible
characters, and is refused (422) when even the largest brief it could make
(``interpret_policy.worst_case_brief``) would not fit ``classes.brief``. It always creates a new
class: ``class_id`` is refused, and a missing title is taken from the request's first words
(``interpret_policy.title_for``; a model never sees a title).

**Admit**: see ``classbeat.design.interpret_admit`` (the input and ``admit``).

**Worker**: the ``design`` stage group's model first (an unusable route fails ``model_config``
before any call), then the ``interpret`` stage: the user's music names (``interpret_music``), the
``interpret`` agent stage (``agent.interpret``) and ``interpret_policy.resolve``. The final fenced
transaction stores ``classes.interpretation`` (``interpretation_row.store``), makes the reading's
short ``name`` the class's title while the title is still the request's first words
(``class_name.adopt``, #1233), and the job completes. It **never enqueues** the design: the worker
cannot reach the jobs API (``tests/session_scan.py``) and the class's one-open-job index would
refuse a design job while this one runs. The browser, or the class page after a reopened tab,
calls ``POST /api/classes/{id}/design-from-interpretation`` (``classbeat.classes.interpretation``).
A failed interpret stores nothing and uses no credit.
"""

from typing import Final

from classbeat.agent import run_stage
from classbeat.agent.interpret import InterpretContext, InterpretResult, interpret_stage
from classbeat.agent.interpret_input import MusicName
from classbeat.agent.records import JobStageHost
from classbeat.design import (
    class_name,
    interpret_composer_policy,
    interpret_policy,
    interpretation_row,
    music_decision,
)
from classbeat.design.interpret_admit import (
    InterpretRequestInput as InterpretRequestInput,  # re-exported: the input models live there
)
from classbeat.design.interpret_admit import (
    MusicOverride as MusicOverride,
)
from classbeat.design.interpret_admit import (
    PickChoice as PickChoice,
)
from classbeat.design.interpret_admit import admit
from classbeat.design.interpret_music import music_options
from classbeat.design.kind import current_runtime, group_model
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.quota.route import with_plan, with_plan_run

KIND: Final = "interpret_request"
STAGES: Final = ("interpret",)


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, InterpretRequestInput) or job_run.approved:
        raise JobFailure("internal")
    model = await group_model(runtime, job_run, given.route_key, "design")  # before any call
    await job_run.stage("interpret")
    mixes = bool(given.swedebeat_mixes) and runtime.swedebeat_enabled
    options = await music_options(job_run, runtime.keyring, given.mowl_connection_id, mixes=mixes)
    gates = given.plan_gates
    sports = tuple(given.sports or ("cycling",))
    ctx = InterpretContext(
        given.request,
        sports=sports,
        max_class_minutes=None if gates is None else gates.max_class_minutes,
        games_allowed=True if gates is None else gates.games,
        music=tuple(MusicName(o.kind, o.name) for o in options),
        attached=len(given.music_sources or ()) if given.composer else 0,
        soundless=bool(given.soundless),
    )
    outcome = await run_stage(
        JobStageHost(job_run),
        model.provider,
        interpret_stage(ctx),
        ctx.first_message(),
        model.agent,
    )
    if not isinstance(outcome.output, InterpretResult):
        raise JobFailure("internal")
    offer = interpret_policy.Offer(
        sports,
        gates,
        options,
        bool(given.pick_ready),
        given.spotify_connection_id is not None,
        given.song_picking is not False,
    ).composed(bool(given.composer))
    override = None if given.music_override is None else given.music_override.model_dump()
    resolved = interpret_policy.resolve(
        outcome.output.request,
        given.request,
        offer,
        sport_override=given.sport_override,
        music_override=override,
    )
    skip = interpret_composer_policy.chosen_topics(
        music=given.music_override is not None or bool(given.music_sources),
        sport=given.sport_override is not None,
    )
    pins, questions = (
        interpret_composer_policy.composer_reading(outcome.output.request, skip)
        if given.composer
        else ([], [])
    )
    decision = music_decision.initial(
        outcome.output.request,
        composer=bool(given.composer) and resolved.brief.sport == "cycling",
        attached=bool(given.music_sources),
        chose=given.music_override is not None,
    )
    from_request = interpret_policy.title_for(given.request)
    stored = interpretation_row.StoredInterpretation(
        job_id=job_run.lease.job_id,
        brief=resolved.brief.stored(),
        music=resolved.music,
        items=resolved.items,
        pinned_songs=pins,
        questions=questions,
        music_decision=decision,
    )

    name = outcome.output.request.name if given.title == from_request else None
    reread = given.reread_of is not None  # #1536: the class keeps its title and its first message

    async def store(fs: FencedSession) -> None:
        await interpretation_row.store(fs, stored, reread=reread)
        if not reread:
            await class_name.adopt(fs, name, from_request)  # #1233: never a title the user chose

    return Complete(store)


INTERPRET_REQUEST: Final = KINDS.register(
    JobKind(
        KIND,
        InterpretRequestInput,
        STAGES,
        with_plan_run(run),
        consumes_credit=False,
        admit=with_plan(admit),
        server_fields=frozenset(
            {
                "route_key",
                "plan_gates",
                "sports",
                "pick_ready",
                "song_picking",
                "swedebeat_mixes",
                "soundless",
                "reread_of",
                "answers",
                "composer",
            }
        ),
    )
)
