from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Sequence

from voice_server.app import ServerApplication
from voice_server.config import ConfigError, load_config


async def run_application(application: ServerApplication) -> None:
    try:
        await application.start()
        await asyncio.Future()
    finally:
        await application.stop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Xiaozhi voice server")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config(arguments.config)
        application = ServerApplication.from_config(config)
    except ConfigError:
        logging.getLogger(__name__).error("invalid voice server configuration")
        return 2
    try:
        asyncio.run(run_application(application))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
