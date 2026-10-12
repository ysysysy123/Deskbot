"""Regression coverage for launcher process ownership and persistent output."""

import importlib.util
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest


spec = importlib.util.spec_from_file_location(
    "deskbot_launcher", Path(__file__).resolve().parents[1] / "deskbot_launcher.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_successful_model_selection_can_be_restored_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "LOG_DIR", tmp_path)
    launcher.save_selection("智谱云端 · 视觉版", "glm-4-flash")
    assert launcher.load_selection() == ("智谱云端 · 视觉版", "glm-4-flash")
    assert set(launcher.json.loads((tmp_path / "selection.json").read_text())) == {"mode", "model"}


def test_port_check_distinguishes_listener_from_recently_closed_connection():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen()
        assert launcher.port_is_in_use(port)
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = server.accept()
            connection.close()
            assert client.recv(1) == b""
    assert not launcher.port_is_in_use(port)


def test_finds_script_relative_to_cwd_without_matching_other_checkout(tmp_path, monkeypatch):
    root = tmp_path / "deskbot"
    other = tmp_path / "other"
    for checkout in (root, other):
        voice = checkout / "server" / "voice"
        voice.mkdir(parents=True)
        (voice / "run.py").write_text("import time\ntime.sleep(30)\n")
    monkeypatch.setattr(launcher, "ROOT", root)
    processes = [
        subprocess.Popen([sys.executable, "-u", "run.py"], cwd=checkout / "server" / "voice", start_new_session=True)
        for checkout in (root, other)
    ]
    try:
        found = launcher.deskbot_process_pids()
        assert processes[0].pid in found["voice"]
        assert processes[1].pid not in found["voice"]
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=3)


@pytest.mark.parametrize("group, expected", [(123, "group"), (987, "pid")])
def test_only_terminates_whole_group_when_service_owns_it(monkeypatch, group, expected):
    calls = []
    monkeypatch.setattr(launcher.os, "getpgid", lambda pid: group)
    monkeypatch.setattr(launcher.os, "getpgrp", lambda: 456)
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: calls.append(("pid", pid, sig)))
    monkeypatch.setattr(launcher.os, "killpg", lambda pid, sig: calls.append(("group", pid, sig)))
    monkeypatch.setattr(launcher, "process_is_running", lambda pid: False)
    launcher.stop_process_group(123)
    assert calls == [(expected, 123, signal.SIGINT)]


def test_preflight_cleans_old_services_once_and_reports_remaining_port(monkeypatch):
    panel = launcher.DeskbotLauncher.__new__(launcher.DeskbotLauncher)
    messages = []
    panel._log = messages.append
    calls = []
    monkeypatch.setattr(launcher, "stop_stale_deskbot_processes", lambda: calls.append("stop") or [123])
    monkeypatch.setattr(launcher, "port_is_in_use", lambda port: port == 8006)
    with pytest.raises(RuntimeError, match="8006"):
        panel._preflight_ports()
    assert calls == ["stop"]
    assert "123" in messages[0]


def test_child_output_is_persisted_and_exit_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "LOG_DIR", tmp_path)
    panel = launcher.DeskbotLauncher.__new__(launcher.DeskbotLauncher)
    panel.events = queue.Queue()
    panel.processes = {}
    panel.process_lock = threading.Lock()
    panel._log = lambda message: panel.events.put(("log", message))
    process = panel._spawn_process("probe", [sys.executable, "-c", "print('Deskbot log probe')"], {})
    process.wait(timeout=3)
    deadline = time.monotonic() + 3
    observed = []
    while time.monotonic() < deadline:
        event = panel.events.get(timeout=3)
        observed.append(event)
        if event[0] == "process_exit":
            break
    assert "Deskbot log probe" in (tmp_path / "probe.log").read_text()
    assert any(event[0] == "process_exit" and event[1] == "probe" and event[3] == 0 for event in observed)
