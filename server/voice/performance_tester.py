"""Measure the configured speech providers without starting the server."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from voice_server.performance import main


if __name__ == "__main__":
    raise SystemExit(main(default_config=Path(__file__).with_name("config.yaml")))
