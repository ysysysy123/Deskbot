"""Load local process settings without replacing an exported environment."""

import os
from pathlib import Path

from dotenv import dotenv_values


def read_environment(path: Path) -> dict[str, str]:
    # Keep values literal, including credentials containing dollar signs.
    return {
        key: value
        for key, value in dotenv_values(path, encoding="utf-8-sig", interpolate=False).items()
        if value is not None
    }


def load_environment(path: Path) -> dict[str, str]:
    values = read_environment(path)
    for key, value in values.items():
        os.environ.setdefault(key, value)
    return values
