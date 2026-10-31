"""Model routes and the enable gate (ADR 0012 section 5; M3c G1).

- ``store``: the SQL of ``model_eval_results`` and ``model_routes``; ``store.record_result`` is the
  one writer of results (the live eval command only), and is not exported here;
- ``routes``: ``set_route``, ``clear_route``, ``list_routes``, ``list_results``,
  ``resolve_route`` and their types (what M5's admin and G2's jobs call);
- ``versions``: the running prompt versions per stage group and the suite version;
- ``jobs``: what a model job's stage group runs on (``GroupModel``, ``ModelSource``);
- ``providers``: ``provider_for_choice`` and ``RouteModels``, the worker's ``ModelSource`` (worker
  only; not imported here, so the web process never loads a provider adapter);
- ``cli``: ``python -m classbeat models list|results|enable|disable``.
"""

from classbeat.jobs.inputs import DEFAULT_ROUTE_KEY
from classbeat.models.routes import (
    ModelChoice,
    ResultView,
    RouteRefused,
    RouteUnusable,
    RouteView,
    bootstrap_choice,
    clear_route,
    list_results,
    list_routes,
    resolve_route,
    set_route,
)

__all__ = [
    "DEFAULT_ROUTE_KEY",
    "ModelChoice",
    "ResultView",
    "RouteRefused",
    "RouteUnusable",
    "RouteView",
    "bootstrap_choice",
    "clear_route",
    "list_results",
    "list_routes",
    "resolve_route",
    "set_route",
]
