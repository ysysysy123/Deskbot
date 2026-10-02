#!/usr/bin/env bash

# Stop Deskbot services left by any previous launcher instance, regardless of PID.
pkill -f 'server/voice/run.py' >/dev/null 2>&1 || true
pkill -f 'server/voice/local_test.py' >/dev/null 2>&1 || true

exec /home/yyy/Apps/miniconda3/envs/xiaozhi/bin/python \
  /home/yyy/desktop/ai/Deskbot/tools/deskbot_launcher.py
