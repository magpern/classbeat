"""``python -m classbeat``: run the web app with uvicorn and JSON logging, or an operator
subcommand (``invite create``, ``admin grant|revoke|mfa-reset``; see ``classbeat.cli``)."""

import argparse
import sys
from collections.abc import Sequence

import uvicorn

from classbeat.cli import add_subcommands, run_command
from classbeat.config import Settings
from classbeat.log import configure_logging
from classbeat.web import create_app

# psycopg's async mode cannot run on the proactor loop uvicorn picks on Windows.
UVICORN_LOOP = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m classbeat", description="Run the classbeat API, or an operator command."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    add_subcommands(parser)
    args = parser.parse_args(argv)

    settings = Settings()
    configure_logging(settings.log_level)
    if args.command is not None:
        return run_command(args, settings)
    # log_config=None: uvicorn's loggers propagate to the root JSON handler. Its access log is
    # off (our middleware logs path and status only, ADR 0003 section 10) and so is its proxy
    # header handling (we read X-Forwarded-For ourselves, from trusted proxies only).
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        log_config=None,
        loop=UVICORN_LOOP,
        access_log=False,
        proxy_headers=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
