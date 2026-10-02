#!/usr/bin/env python3
"""A small local control panel for Deskbot and its local LLM endpoint."""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext
import tkinter as tk
from tkinter import ttk

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "server" / "voice"
CONFIG_PATH = VOICE_DIR / "config.yaml"
NINFER_TEXT_START = Path.home() / ".local" / "bin" / "ninfer-chat-start-text"
NINFER_VISION_START = Path.home() / ".local" / "bin" / "ninfer-chat-start"
NINFER_STOP = Path.home() / ".local" / "bin" / "ninfer-chat-stop"
NINFER_BASE_URL = "http://127.0.0.1:8084/v1"
NINFER_MODELS_URL = "http://127.0.0.1:8084/v1/models"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
NINFER_MODEL = "qwen3.8-27b"
TEST_PAGE_URL = "http://127.0.0.1:8006/"
HEALTH_URL = "http://127.0.0.1:8003/health"
NINFER_CONTAINER = "ninfer-bonsai"
ZHIPU_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_DEFAULT_TEXT_MODEL = "glm-4-flash"
ZHIPU_DEFAULT_VISION_MODEL = "glm-4v-flash"

MODES = {
    "ninfer · 文字版": ("ninfer", False),
    "ninfer · 视觉版": ("ninfer", True),
    "Ollama · 文字模型": ("ollama", False),
    "Ollama · 视觉模型": ("ollama", True),
    "智谱云端 · 文字版": ("zhipu", False),
    "智谱云端 · 视觉版": ("zhipu", True),
}


def http_json(url: str, timeout: float = 2.0) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_model_names() -> list[str]:
    data = http_json(OLLAMA_TAGS_URL)
    return [item.get("name", "") for item in data.get("models", []) if item.get("name")]


def zhipu_settings() -> dict[str, str]:
    """Read the saved remote configuration without exposing or rewriting secrets."""
    values = dotenv_values(VOICE_DIR / ".env", encoding="utf-8-sig", interpolate=False)

    def value(name: str, default: str = "") -> str:
        configured = values.get(name)
        return str(configured).strip() if configured else default

    base_url = value("VOICE_LLM_BASE_URL", ZHIPU_DEFAULT_BASE_URL)
    api_key = value("VOICE_LLM_API_KEY")
    return {
        "base_url": base_url,
        "model": value("VOICE_LLM_MODEL", ZHIPU_DEFAULT_TEXT_MODEL),
        "api_key": api_key,
        "vision_base_url": value("VOICE_VISION_BASE_URL") or base_url,
        "vision_model": value("VOICE_VISION_MODEL", ZHIPU_DEFAULT_VISION_MODEL),
        "vision_api_key": value("VOICE_VISION_API_KEY") or api_key,
    }


def port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def port_is_in_use(port: int) -> bool:
    """Check local port ownership without sending invalid data to the service."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def running_ninfer() -> tuple[bool, bool]:
    """Return whether the known container is running and whether it has vision."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{.State.Running}}|{{join .Config.Cmd " "}}',
                NINFER_CONTAINER,
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, False
    if result.returncode != 0:
        return False, False
    running, _, command = result.stdout.strip().partition("|")
    return running == "true", "--vision" in command


class DeskbotLauncher:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Deskbot 本地服务器")
        self.root.tk.call("tk", "appname", "DeskbotLocalServer")
        self.root.geometry("1020x780")
        self.root.minsize(900, 700)
        self.root.option_add("*Font", ("song ti", 16))
        self.root.option_add("*TCombobox*Listbox.font", ("song ti", 16))
        style = ttk.Style(self.root)
        style.configure(".", font=("song ti", 16))
        style.configure("TButton", font=("song ti", 16), padding=(14, 10))
        style.configure("TCombobox", font=("song ti", 16), padding=6)
        style.configure("TLabelframe.Label", font=("song ti", 16, "bold"))

        self.events: queue.Queue[tuple] = queue.Queue()
        self.processes: dict[str, subprocess.Popen] = {}
        self.process_lock = threading.Lock()
        self.busy = False
        self.ninfer_started_here = False
        self.ninfer_mode: bool | None = None
        self.ollama_models: list[str] = []

        self.mode_var = tk.StringVar(value="ninfer · 文字版")
        self.model_var = tk.StringVar(value=NINFER_MODEL)
        self.model_status_var = tk.StringVar(value="模型：等待选择")
        self.server_status_var = tk.StringVar(value="Deskbot：未启动")

        self._build_ui()
        self._on_mode_change()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_events)
        self.refresh_ollama_models()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text="Deskbot 本地服务器", font=("song ti", 25, "bold"))
        title.pack(anchor="w")
        ttk.Label(
            outer,
            text="选择本地或智谱云端模型，一键启动语音服务和浏览器测试页。",
        ).pack(anchor="w", pady=(2, 12))

        selection = ttk.LabelFrame(outer, text="模型选择", padding=12)
        selection.pack(fill="x")
        selection.columnconfigure(1, weight=1)
        ttk.Label(selection, text="运行模式").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.mode_combo = ttk.Combobox(
            selection,
            textvariable=self.mode_var,
            values=tuple(MODES),
            state="readonly",
        )
        self.mode_combo.grid(row=0, column=1, sticky="ew", pady=4)
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        ttk.Label(selection, text="模型").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.model_combo = ttk.Combobox(selection, textvariable=self.model_var, state="disabled")
        self.model_combo.grid(row=1, column=1, sticky="ew", pady=4)
        self.refresh_button = ttk.Button(
            selection, text="刷新 Ollama 模型", command=self.refresh_ollama_models
        )
        self.refresh_button.grid(row=1, column=2, padx=(8, 0), pady=4)

        state = ttk.Frame(outer, padding=(2, 10, 2, 6))
        state.pack(fill="x")
        ttk.Label(state, textvariable=self.model_status_var).pack(anchor="w")
        ttk.Label(state, textvariable=self.server_status_var).pack(anchor="w", pady=(3, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(2, 8))
        self.start_button = ttk.Button(
            controls, text="启动模型 + Deskbot 服务", command=self.start_services
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            controls, text="停止本面板启动的服务", command=self.stop_services, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.open_button = ttk.Button(
            controls, text="打开测试页", command=lambda: webbrowser.open(TEST_PAGE_URL), state="disabled"
        )
        self.open_button.pack(side="left", padx=(8, 0))

        note = (
            "视觉模式会启用 Deskbot 摄像头/视觉入口；请在浏览器中允许摄像头权限。"
            "智谱模式读取现有 .env 的密钥；Ollama 视觉模式需要支持图片输入的模型。"
            "选择只作用于本次启动，不修改 .env。"
            "语音合成仍使用 Edge TTS，需要联网。"
        )
        ttk.Label(outer, text=note, wraplength=960, justify="left").pack(anchor="w", pady=(0, 8))

        log_frame = ttk.LabelFrame(outer, text="启动日志", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log_view = scrolledtext.ScrolledText(
            log_frame, height=17, wrap="word", state="disabled", font=("song ti", 13)
        )
        self.log_view.pack(fill="both", expand=True)

    def _update_model_input(self) -> None:
        kind, vision = MODES[self.mode_var.get()]
        if kind == "ninfer":
            self.model_var.set(NINFER_MODEL)
            self.model_combo.configure(state="disabled", values=(NINFER_MODEL,))
            self.refresh_button.configure(state="disabled")
        elif kind == "ollama":
            self.model_combo.configure(state="normal", values=self.ollama_models)
            self.refresh_button.configure(state="normal")
            if self.model_var.get() not in self.ollama_models:
                self.model_var.set(self.ollama_models[0] if self.ollama_models else "")
        else:
            settings = zhipu_settings()
            model = settings["model"]
            if vision:
                model += " + " + settings["vision_model"]
            self.model_var.set(model)
            self.model_combo.configure(state="disabled", values=(model,))
            self.refresh_button.configure(state="disabled")

    def _on_mode_change(self, _event=None) -> None:
        self._update_model_input()
        kind, _ = MODES[self.mode_var.get()]
        if kind == "zhipu":
            configured = bool(zhipu_settings()["api_key"])
            detail = "已读取 .env 密钥" if configured else ".env 中缺少 API Key"
            self.model_status_var.set("模型：已选择 " + self.mode_var.get() + "；" + detail)
        else:
            self.model_status_var.set("模型：已选择 " + self.mode_var.get())

    def _log(self, message: str) -> None:
        self.events.put(("log", message))

    def _set_status(self, *, model: str | None = None, server: str | None = None) -> None:
        self.events.put(("status", model, server))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.start_button.configure(state=state)
        self.mode_combo.configure(state="disabled" if busy else "readonly")
        kind, _ = MODES[self.mode_var.get()]
        if kind == "ollama":
            self.model_combo.configure(state=state)
            self.refresh_button.configure(state=state)
        else:
            self.model_combo.configure(state="disabled")
            self.refresh_button.configure(state="disabled")
        self.stop_button.configure(state="disabled" if busy else "normal")

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "log":
                self.log_view.configure(state="normal")
                self.log_view.insert("end", event[1] + "\n")
                lines = int(self.log_view.index("end-1c").split(".")[0])
                if lines > 1800:
                    self.log_view.delete("1.0", f"{lines - 1400}.0")
                self.log_view.see("end")
                self.log_view.configure(state="disabled")
            elif kind == "status":
                if event[1] is not None:
                    self.model_status_var.set(event[1])
                if event[2] is not None:
                    self.server_status_var.set(event[2])
            elif kind == "models":
                self.ollama_models = event[1]
                self.model_combo.configure(values=self.ollama_models)
                current = self.model_var.get()
                if self.mode_var.get().startswith("Ollama") and current not in self.ollama_models:
                    self.model_var.set(self.ollama_models[0] if self.ollama_models else "")
                if event[2] and MODES[self.mode_var.get()][0] == "ollama":
                    self.model_status_var.set(event[2])
            elif kind == "error":
                messagebox.showerror(event[1], event[2], parent=self.root)
            elif kind == "done":
                self._set_busy(False)
                if event[1]:
                    self.open_button.configure(state="normal")
                if event[2]:
                    self.root.destroy()
                    return
        self.root.after(100, self._drain_events)

    def refresh_ollama_models(self) -> None:
        if self.busy:
            return
        if MODES[self.mode_var.get()][0] == "ollama":
            self.model_status_var.set("模型：正在查询 Ollama…")
        threading.Thread(target=self._refresh_models_worker, daemon=True).start()

    def _refresh_models_worker(self) -> None:
        try:
            models = ollama_model_names()
            status = f"Ollama：找到 {len(models)} 个本地模型" if models else "Ollama：当前没有已安装模型"
            self.events.put(("models", models, status))
        except Exception:
            self.events.put(("models", [], "Ollama：接口暂不可用；可先启动服务或安装本地模型"))

    def _owned_process_running(self, name: str) -> bool:
        with self.process_lock:
            process = self.processes.get(name)
            return process is not None and process.poll() is None

    def _preflight_ports(self) -> bool:
        occupied = []
        for port, process_name in ((8000, "voice"), (8003, "voice"), (8006, "tester")):
            if port_is_in_use(port) and not self._owned_process_running(process_name):
                occupied.append(str(port))
        if occupied:
            messagebox.showerror(
                "端口已被占用",
                "这些端口已有其他进程在监听：" + ", ".join(occupied)
                + "。请先停止旧的 Deskbot 服务，再从这个面板启动。",
                parent=self.root,
            )
            return False
        return True

    def start_services(self) -> None:
        if self.busy:
            return
        if not self._preflight_ports():
            return

        mode = self.mode_var.get()
        kind, vision = MODES[mode]
        model = self.model_var.get().strip()
        if kind == "ollama" and not model:
            messagebox.showerror("缺少模型", "请选择或填写一个 Ollama 本地模型。", parent=self.root)
            return
        if kind == "zhipu":
            settings = zhipu_settings()
            if not settings["api_key"]:
                messagebox.showerror(
                    "缺少智谱 API Key",
                    "请先在 server/voice/.env 中填写 VOICE_LLM_API_KEY。",
                    parent=self.root,
                )
                return
            model = settings["model"]

        stop_existing_ninfer = False
        if kind == "ninfer":
            active, active_vision = running_ninfer()
            if active and active_vision != vision:
                target = "视觉版" if vision else "文字版"
                if not messagebox.askyesno(
                    "切换 ninfer 模式",
                    f"ninfer 当前正在运行。切换到{target}会重启 ninfer，并中断当前使用它的连接。继续吗？",
                    parent=self.root,
                ):
                    return
        elif running_ninfer()[0]:
            stop_existing_ninfer = messagebox.askyesno(
                "ninfer 正在运行",
                f"切换到“{mode}”时是否先停止 ninfer 并释放显存？这会中断正在使用 ninfer 的连接。",
                parent=self.root,
            )

        self._set_busy(True)
        self.open_button.configure(state="disabled")
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        self.log_view.configure(state="disabled")
        self._set_status(model="模型：正在启动 " + mode, server="Deskbot：正在启动")
        threading.Thread(
            target=self._start_worker,
            args=(kind, vision, model, stop_existing_ninfer),
            daemon=True,
        ).start()

    def _run_helper(self, script: Path, label: str, timeout: int = 100) -> None:
        if not script.is_file():
            raise RuntimeError(f"找不到启动脚本：{script}")
        self._log(f"正在执行：{label}")
        try:
            result = subprocess.run(
                [str(script)],
                cwd=VOICE_DIR,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"{label}超时；请查看 Docker 状态和启动日志。") from error
        for line in (result.stdout + result.stderr).splitlines():
            self._log(line)
        if result.returncode != 0:
            raise RuntimeError(f"{label}失败，退出码 {result.returncode}。")

    def _start_ninfer(self, vision: bool) -> None:
        active, active_vision = running_ninfer()
        if active and active_vision == vision:
            self._log("已复用正在运行的 ninfer 服务。")
            self.ninfer_mode = vision
        else:
            script = NINFER_VISION_START if vision else NINFER_TEXT_START
            label = "启动 ninfer 视觉版" if vision else "启动 ninfer 文字版"
            self._run_helper(script, label)
            self.ninfer_started_here = True
            self.ninfer_mode = vision

        models = http_json(NINFER_MODELS_URL, timeout=5).get("data", [])
        ids = [item.get("id") for item in models]
        if NINFER_MODEL not in ids:
            raise RuntimeError(f"ninfer 已响应，但模型列表中没有 {NINFER_MODEL}。")
        self._log(f"ninfer 模型可用：{NINFER_MODEL}")

    def _ensure_ollama(self, model: str) -> None:
        try:
            models = ollama_model_names()
        except Exception:
            binary = shutil.which("ollama")
            if not binary:
                raise RuntimeError("找不到 Ollama 命令，请先安装或启动 Ollama。")
            self._log("Ollama 接口未响应，尝试启动本机 ollama serve…")
            process = self._spawn_process("ollama", [binary, "serve"], os.environ.copy())
            deadline = time.monotonic() + 30
            models = []
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    models = ollama_model_names()
                    break
                except Exception:
                    time.sleep(1)
            if not models:
                raise RuntimeError("Ollama 服务仍不可用，或尚未安装本地模型。")

        if model not in models:
            installed = ", ".join(models) if models else "无"
            raise RuntimeError(
                f"Ollama 中找不到模型 {model}。当前本地模型：{installed}。"
                "先运行 ollama pull <模型名> 下载模型，再刷新列表。"
            )
        self._log(f"Ollama 模型可用：{model}")

    def _spawn_process(self, name: str, args: list[str], env: dict[str, str]) -> subprocess.Popen:
        process = subprocess.Popen(
            args,
            cwd=VOICE_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        with self.process_lock:
            self.processes[name] = process
        threading.Thread(target=self._read_process_output, args=(name, process), daemon=True).start()
        self._log(f"已启动 {name} 进程，PID {process.pid}")
        return process

    def _read_process_output(self, name: str, process: subprocess.Popen) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self._log(f"[{name}] {line.rstrip()}")

    def _wait_for_health(self, process: subprocess.Popen, timeout: int = 180) -> None:
        deadline = time.monotonic() + timeout
        last_error = "尚未响应"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Deskbot 语音服务已退出，退出码 {process.returncode}。")
            try:
                data = http_json(HEALTH_URL, timeout=2)
                if data.get("status") == "ok":
                    return
                last_error = f"健康检查返回 {data}"
            except Exception as error:
                last_error = str(error)
            time.sleep(1)
        raise RuntimeError(f"等待 Deskbot 健康检查超时：{last_error}")

    def _wait_for_port(self, process: subprocess.Popen, port: int, timeout: int = 30) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"本地测试页已退出，退出码 {process.returncode}。")
            if port_is_open(port):
                return
            time.sleep(0.5)
        raise RuntimeError(f"等待本地测试页端口 {port} 超时。")

    def _start_worker(self, kind: str, vision: bool, model: str, stop_existing_ninfer: bool) -> None:
        try:
            self._stop_processes({"voice", "tester"})

            if stop_existing_ninfer:
                self._run_helper(NINFER_STOP, "停止 ninfer 并释放显存", timeout=30)
                self.ninfer_started_here = False
                self.ninfer_mode = None

            if kind == "ninfer":
                self._set_status(model="模型：启动 ninfer", server=None)
                self._start_ninfer(vision)
                base_url = NINFER_BASE_URL
                api_key = "local"
                vision_base_url = base_url
                vision_model = model
                vision_api_key = api_key
            elif kind == "ollama":
                self._set_status(model="模型：连接 Ollama", server=None)
                self._ensure_ollama(model)
                base_url = OLLAMA_BASE_URL
                api_key = "local"
                vision_base_url = base_url
                vision_model = model
                vision_api_key = api_key
            else:
                settings = zhipu_settings()
                if not settings["api_key"]:
                    raise RuntimeError("server/voice/.env 中没有配置智谱 VOICE_LLM_API_KEY。")
                self._set_status(model="模型：连接智谱云端", server=None)
                base_url = settings["base_url"]
                model = settings["model"]
                api_key = settings["api_key"]
                vision_base_url = settings["vision_base_url"]
                vision_model = settings["vision_model"]
                vision_api_key = settings["vision_api_key"]
                self._log(f"已读取智谱配置：文字 {model}" + (f"，视觉 {vision_model}" if vision else ""))

            env = os.environ.copy()
            runtime_bin = Path(sys.executable).resolve().parent
            inherited_path = env.get("PATH", "")
            env["PATH"] = str(runtime_bin) + (os.pathsep + inherited_path if inherited_path else "")
            ffmpeg_path = runtime_bin / "ffmpeg"
            if not ffmpeg_path.is_file():
                raise RuntimeError(f"xiaozhi 环境中找不到 FFmpeg：{ffmpeg_path}")
            env["FFMPEG"] = str(ffmpeg_path)
            self._log(f"FFmpeg 已就绪：{ffmpeg_path}")
            env["VOICE_LLM_BASE_URL"] = base_url
            env["VOICE_LLM_MODEL"] = model
            env["VOICE_LLM_API_KEY"] = api_key
            env["VOICE_VISION_ENABLED"] = "true" if vision else "false"
            env["VOICE_VISION_BASE_URL"] = vision_base_url if vision else ""
            env["VOICE_VISION_MODEL"] = vision_model
            env["VOICE_VISION_API_KEY"] = vision_api_key if vision else ""
            env["VOICE_MCP_ENABLED"] = "true" if vision else "false"
            warning_filter = "ignore:pkg_resources is deprecated as an API:UserWarning"
            inherited_warnings = env.get("PYTHONWARNINGS", "").strip()
            env["PYTHONWARNINGS"] = (
                inherited_warnings + "," + warning_filter if inherited_warnings else warning_filter
            )

            selected_models = model + (f" + {vision_model}" if vision and vision_model != model else "")
            self._set_status(
                model=f"模型：{mode_name(kind, vision)} · {selected_models}",
                server="Deskbot：加载 ASR 并启动",
            )
            voice = self._spawn_process(
                "voice",
                [sys.executable, str(VOICE_DIR / "run.py"), "--config", str(CONFIG_PATH)],
                env,
            )
            self._wait_for_health(voice)
            self._log("语音服务健康检查通过（8003）。")

            tester = self._spawn_process(
                "tester",
                [sys.executable, str(VOICE_DIR / "local_test.py"), "--config", str(CONFIG_PATH)],
                env,
            )
            self._wait_for_port(tester, 8006)
            self._log("浏览器测试页已就绪（8006）。")
            self._log("浏览器测试页已启用在线设备列表（每 2 秒刷新）。")
            self._log("Deskbot 已启动成功，可以开始对话。")
            self._set_status(server="Deskbot：运行中（WS 8000 / OTA 8003 / 测试页 8006）")
            self.events.put(("done", True, False))
            webbrowser.open(TEST_PAGE_URL)
        except Exception as error:
            self._stop_processes({"voice", "tester"})
            self._log("启动失败：" + str(error))
            self._set_status(server="Deskbot：启动失败，可检查日志后重试")
            self.events.put(("error", "启动失败", str(error)))
            self.events.put(("done", False, False))

    @staticmethod
    def _stop_one(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=8)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except OSError:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def _stop_processes(self, names: set[str]) -> None:
        with self.process_lock:
            selected = [(name, self.processes.pop(name)) for name in tuple(self.processes) if name in names]
        for name, process in selected:
            self._log(f"正在停止 {name}…")
            self._stop_one(process)

    def stop_services(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self._set_status(server="Deskbot：正在停止本面板启动的服务")
        threading.Thread(target=self._stop_worker, args=(False,), daemon=True).start()

    def _stop_worker(self, close_after: bool) -> None:
        try:
            self._stop_processes({"voice", "tester", "ollama"})
            if self.ninfer_started_here:
                self._run_helper(NINFER_STOP, "停止本面板启动的 ninfer", timeout=30)
                self.ninfer_started_here = False
                self.ninfer_mode = None
            self._set_status(model="模型：面板启动的模型进程已停止", server="Deskbot：已停止")
            self._log("本面板启动的服务已停止。")
        except Exception as error:
            self._log("停止时出错：" + str(error))
            self.events.put(("error", "停止服务时出错", str(error)))
        self.events.put(("done", False, close_after))

    def _on_close(self) -> None:
        if self.busy:
            messagebox.showinfo("操作进行中", "请等当前启动或停止操作完成后再关闭面板。", parent=self.root)
            return
        with self.process_lock:
            has_children = any(process.poll() is None for process in self.processes.values())
        if has_children or self.ninfer_started_here:
            if not messagebox.askyesno(
                "退出面板",
                "退出时会停止本面板启动的 Deskbot、Ollama 和 ninfer 进程；复用的系统 Ollama 服务会继续运行。现在停止并退出吗？",
                parent=self.root,
            ):
                return
            self._set_busy(True)
            threading.Thread(target=self._stop_worker, args=(True,), daemon=True).start()
            return
        self.root.destroy()


def mode_name(kind: str, vision: bool) -> str:
    if kind == "ninfer":
        return "ninfer 视觉版" if vision else "ninfer 文字版"
    if kind == "ollama":
        return "Ollama 视觉模型" if vision else "Ollama 文字模型"
    return "智谱云端视觉版" if vision else "智谱云端文字版"


def main() -> None:
    root = tk.Tk()
    DeskbotLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
