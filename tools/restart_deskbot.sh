#!/usr/bin/env bash

set -euo pipefail

DESKBOT_TOOLS="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DESKBOT_PYTHON="${DESKBOT_PYTHON:-$HOME/Apps/miniconda3/envs/xiaozhi/bin/python}"

# Use the same precise process matching and shutdown wait as the panel.
exec "$DESKBOT_PYTHON" "$DESKBOT_TOOLS/deskbot_launcher.py" --restart
