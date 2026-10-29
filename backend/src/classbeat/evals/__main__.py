"""``python -m classbeat.evals smoke --live --max-usd N`` (see ``classbeat.evals.live``) and
``python -m classbeat.evals compare RESULT.json ... --out DIR`` (offline, see
``classbeat.evals.compare``).

In the stack, on the worker (the only container with the model key):
``docker compose exec worker python -m classbeat.evals smoke --live``; it prints its plan and the
cost ceiling and stops; run it again with ``--max-usd`` to accept the spend.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from classbeat.config import Settings
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
    compare = sub.add_parser("compare", help="compare saved results offline (no model, no network)")
    compare.add_argument("results", nargs="+", type=Path, help="JSON files written by --out")
    compare.add_argument("--out", type=Path, required=True, help="directory for compare.md/html")
    args = parser.parse_args(argv)
    if args.command == "compare":
        return main_compare(args.results, args.out)
    settings = Settings()
    configure_logging(settings.log_level)
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


if __name__ == "__main__":
    sys.exit(main())
