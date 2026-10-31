"""``python -m classbeat models list | results | enable | disable`` (ADR 0012 section 5).

Until M5's admin exists, the operator sets routes with this CLI, in the **worker** container (the
one with the model keys, so ``list`` can say whether a route is usable)::

    docker compose exec worker python -m classbeat models list
    docker compose exec worker python -m classbeat models results [--provider P] [--model M]
    docker compose exec worker python -m classbeat models enable KEY GROUP PROVIDER MODEL EFFORT
    docker compose exec worker python -m classbeat models disable ROUTE_KEY GROUP

``enable`` prints the result it would rely on and changes nothing without ``--yes``. It runs as the
app role through ``classbeat.models.routes`` (the same checks as M5's admin); ``set_by`` is
``cli``. Output: ids, versions, counts, costs and reason codes; never a key or a key file's
content (``list`` says only whether the key file is present, as ``no_key``).
"""

import argparse
from typing import Any, TextIO

from classbeat.config import Settings
from classbeat.db import create_engine, create_session_factory, system_session, verify_database_role
from classbeat.models import routes
from classbeat.models.routes import ResultView, RouteRefused

ACTOR = "cli"


def add_models_subcommands(sub: Any) -> None:
    """``models list | results | enable | disable`` (``sub``: the subparsers)."""
    models = sub.add_parser(
        "models", help="model routes and recorded eval results (ADR 0012)"
    ).add_subparsers(dest="action", required=True)
    models.add_parser("list", help="every route, its result, whether it is stale or unusable")
    results = models.add_parser("results", help="the recorded eval results, newest first")
    results.add_argument("--provider")
    results.add_argument("--model")
    enable = models.add_parser(
        "enable", help="route a stage group of ROUTE_KEY to a model with a recorded pass"
    )
    for name in ("route_key", "group", "provider", "model", "effort"):
        enable.add_argument(name)
    enable.add_argument("--yes", action="store_true", help="really set the route")
    disable = models.add_parser("disable", help="remove a route (back to the bootstrap model)")
    disable.add_argument("route_key")
    disable.add_argument("group")


def _usd(micros: int) -> str:
    return f"${micros / 1e6:.4f}"


def _result_line(r: ResultView) -> str:
    return (
        f"{r.id} {r.recorded_at} {r.provider} {r.model} {r.effort} {r.stage_group} "
        f"suite {r.suite_version} {r.status} cost {_usd(r.total_cost_micros)} "
        f"classbeat {r.classbeat_version} {'current' if r.current else 'not current'}"
    )


async def run(args: argparse.Namespace, settings: Settings, out: TextIO, err: TextIO) -> int:
    engine = create_engine(settings)
    try:
        await verify_database_role(engine)
        factory = create_session_factory(engine)
        async with system_session(factory) as db:
            if args.action == "list":
                views = await routes.list_routes(db)
                if not views:
                    print("no routes: every job uses the bootstrap model", file=out)
                for v in views:
                    unusable = routes.unusable_reason(settings, v.provider, v.model, v.effort)
                    if unusable is None and "newer_failure" in v.stale:
                        unusable = "newer_failure"  # the worker refuses it (no standing pass)
                    state = f"unusable: {unusable}" if unusable else "usable"
                    stale = f" stale: {', '.join(v.stale)}" if v.stale else ""
                    print(
                        f"{v.route_key} {v.stage_group} {v.provider} {v.model} {v.effort} "
                        f"{state}{stale} result {v.result.id} set {v.set_at} by {v.set_by}",
                        file=out,
                    )
                return 0
            if args.action == "results":
                found = await routes.list_results(db, args.provider, args.model)
                if not found:
                    print("no recorded results", file=out)
                for r in found:
                    print(_result_line(r), file=out)
                return 0
            if args.action == "disable":
                await routes.clear_route(db, args.route_key, args.group, ACTOR)
                print(f"route {args.route_key} {args.group} removed", file=err)
                return 0
            row = await routes.gate_result(
                db,
                args.route_key,
                args.group,
                args.provider,
                args.model,
                args.effort,
                settings=settings,
            )
            print("the route will rely on: " + _result_line(ResultView.of(row)), file=out)
            if not args.yes:
                print("nothing changed: add --yes to set the route", file=err)
                return 1
            await routes.set_route(
                db,
                args.route_key,
                args.group,
                args.provider,
                args.model,
                args.effort,
                ACTOR,
                settings=settings,
            )
            print(f"route {args.route_key} {args.group} set", file=err)
            unusable = routes.unusable_reason(settings, args.provider, args.model, args.effort)
            if unusable:
                print(f"warning: the worker cannot use it now: {unusable}", file=err)
            return 0
    except RouteRefused as refused:
        print(f"refused: {refused.reason}", file=err)
        return 1
    finally:
        await engine.dispose()
