"""``python -m classbeat.evals smoke --live --max-usd N`` (see ``classbeat.evals.live``),
``python -m classbeat.evals suite --live --provider P --model M --effort E --repeat N --max-usd N
--out FILE [--record --authorization TEXT]`` (see ``classbeat.evals.suite_live``) and
``python -m classbeat.evals compare RESULT.json ... --out DIR`` (offline, see
``classbeat.evals.compare``).

``suite`` is two layers (ADR 0012 §4.4): this entry checks every precondition
(``suite_live.check_preconditions``) and then calls ``suite_live.main_suite`` with the real
provider factory (``classbeat.models.providers.eval_provider_for_choice``: ``provider_for_choice``
with DeepSeek's eval-only path, so a disabled DeepSeek model can be evaluated), the real allowlist
installer and the worker's database session; no argument reaches those seams.

In the stack, on the worker (the only container with the model key):
``docker compose exec worker python -m classbeat.evals smoke --live``; it prints its plan and the
cost ceiling and stops; run it again with ``--max-usd`` to accept the spend.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from classbeat.config import Settings
from classbeat.evals import suite_live
from classbeat.evals.compare import main_compare
from classbeat.evals.live import main_live
from classbeat.log import configure_logging


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m classbeat.evals")
    sub = parser.add_subparsers(dest="command", required=True)
    smoke = sub.add_parser("smoke", help="the smoke set against the real model (owner only)")
    smoke.add_argument("--live", action="store_true", help="use the real model (costs money)")
    smoke.add_argument("--max-usd", help="accept spending up to this many dollars")
    smoke.add_argument("--case", action="append", dest="cases", help="run only this case")
    smoke.add_argument("--out", type=Path, help="write the results as JSON here")
    smoke.add_argument("--model", help="a priced model to run instead of CLASSBEAT_AGENT_MODEL")
    smoke.add_argument("--effort", help="effort instead of CLASSBEAT_AGENT_EFFORT")
    smoke.add_argument("--repeat", type=int, default=1, help="run each case this many times")
    suite = sub.add_parser("suite", help="the regression suite against a catalog model (owner)")
    suite.add_argument("--live", action="store_true", help="use the real model (costs money)")
    suite.add_argument("--provider", required=True, help="a catalog provider")
    suite.add_argument("--model", required=True, help="a catalog model of that provider")
    suite.add_argument("--effort", required=True, help="an effort the catalog entry lists")
    suite.add_argument("--repeat", type=int, default=3, help="run each case this many times")
    suite.add_argument("--max-usd", help="accept spending up to this many dollars")
    suite.add_argument("--out", type=Path, required=True, help="the format-2 results file")
    suite.add_argument("--record", action="store_true", help="record the verdict for the gate")
    suite.add_argument("--authorization", help="the owner's go-ahead for --record (1-200 chars)")
    compare = sub.add_parser("compare", help="compare saved results offline (no model, no network)")
    compare.add_argument("results", nargs="+", type=Path, help="JSON files written by --out")
    compare.add_argument("--out", type=Path, required=True, help="directory for compare.md/html")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return main_compare(args.results, args.out)
    settings = Settings()
    configure_logging(settings.log_level)
    if args.command == "suite":
        return main_suite_entry(settings, args)
    return main_live(
        settings,
        live=args.live,
        max_usd=args.max_usd,
        case_names=args.cases,
        out_path=args.out,
        model=args.model,
        effort=args.effort,
        repeat=args.repeat,
    )


def main_suite_entry(settings: Settings, args: argparse.Namespace) -> int:
    """The CLI layer of ``suite``: the preconditions, then the real seams."""
    try:
        choice = suite_live.check_preconditions(
            settings,
            live=args.live,
            provider=args.provider,
            model=args.model,
            effort=args.effort,
        )
    except suite_live.SuiteRefused as refused:
        print(str(refused), file=sys.stderr)
        return suite_live.REFUSED
    from classbeat.models.providers import eval_provider_for_choice

    return suite_live.main_suite(
        settings,
        choice=choice,
        repeat=args.repeat,
        max_usd=args.max_usd,
        out_path=args.out,
        record_run=args.record,
        authorization=args.authorization,
        provider_factory=eval_provider_for_choice,
        install=suite_live.install_choice_allowlist,
        open_session=suite_live.worker_session(settings),
    )


if __name__ == "__main__":
    sys.exit(main())
