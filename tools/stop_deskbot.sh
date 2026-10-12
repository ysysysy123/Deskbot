#!/usr/bin/env bash

set -euo pipefail

DESKBOT_TOOLS="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DESKBOT_PYTHON="${DESKBOT_PYTHON:-$HOME/Apps/miniconda3/envs/xiaozhi/bin/python}"

exec "$DESKBOT_PYTHON" "$DESKBOT_TOOLS/deskbot_launcher.py" --stop
