#!/usr/bin/env bash

pkill -TERM -f 'server/voice/run.py' >/dev/null 2>&1 || true
pkill -TERM -f 'server/voice/local_test.py' >/dev/null 2>&1 || true
pkill -TERM -f '/home/yyy/desktop/ai/Deskbot/tools/deskbot_launcher.py' >/dev/null 2>&1 || true
