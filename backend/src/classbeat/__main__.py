"""``python -m classbeat``: run the web app with uvicorn and JSON logging."""

import argparse
import sys
from collections.abc import Sequence

import uvicorn

from classbeat.config import Settings
from classbeat.log import configure_logging
from classbeat.web import create_app

# psycopg's async mode cannot run on the proactor loop uvicorn picks on Windows.
UVICORN_LOOP = "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m classbeat", description="Run the classbeat API."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    settings = Settings()
    configure_logging(settings.log_level)
    # log_config=None: uvicorn's loggers propagate to the root JSON handler.
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        log_config=None,
        loop=UVICORN_LOOP,
    )


if __name__ == "__main__":
    main()
