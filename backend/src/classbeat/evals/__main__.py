"""``python -m classbeat.evals smoke --live --max-usd N`` (see ``classbeat.evals.live``).

In the stack, on the worker (the only container with the model key):
``docker compose exec worker python -m classbeat.evals smoke --live``; it prints its plan and the
cost ceiling and stops; run it again with ``--max-usd`` to accept the spend.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from classbeat.config import Settings
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
    args = parser.parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    return main_live(
        settings,
        live=args.live,
        max_usd=args.max_usd,
        case_names=args.cases,
        out_path=args.out,
    )


if __name__ == "__main__":
    sys.exit(main())
