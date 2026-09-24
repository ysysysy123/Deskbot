from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any
import wave

from voice_server.config import AppConfig, load_config


async def read_audio(path: Path) -> bytes:
    """Read 16 kHz mono PCM directly; use the configured FFmpeg for other files."""
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getframerate(), source.getsampwidth(), source.getnchannels(), source.getcomptype()) == (16000, 2, 1, "NONE"):
                pcm = source.readframes(source.getnframes())
            else:
                pcm = None
    except (wave.Error, EOFError):
        pcm = None
    if pcm is None:
        from voice_server.audio.transcoder import FFmpegTranscoder

        pcm = await FFmpegTranscoder(sample_rate=16000).to_pcm(path.read_bytes())
    if not pcm:
        raise ValueError("empty audio")
    return pcm


def create_provider(kind: str, config: AppConfig) -> Any:
    # Imports stay here so --help and unrelated modules need no model packages.
    if kind == "asr":
        model_path = Path(config.asr.model_path)
        if not (model_path / "model.pt").is_file():
            raise FileNotFoundError("SenseVoice model.pt is missing")
        from voice_server.providers.sensevoice import SenseVoiceASRProvider

        return SenseVoiceASRProvider.from_model_path(
            config.asr.model_path, max_concurrency=config.asr.max_concurrency
        )
    if kind == "llm":
        from voice_server.providers.openai_compatible import OpenAICompatibleLLMProvider

        return OpenAICompatibleLLMProvider(
            base_url=config.llm.base_url,
            model=config.llm.model,
            api_key=config.llm.api_key,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            timeout_s=config.llm.timeout_s,
        )
    from voice_server.providers.edge_tts import EdgeTTSProvider

    return EdgeTTSProvider(voice=config.tts.voice, rate=config.tts.rate, volume=config.tts.volume)


async def measure(kind: str, provider: Any, config: AppConfig, text: str, pcm: bytes) -> dict[str, Any]:
    started = time.perf_counter()
    if kind == "asr":
        transcript = await provider.transcribe(pcm, 16000)
        elapsed = time.perf_counter() - started
        duration = len(pcm) / (16000 * 2)
        return {
            "first_response_ms": elapsed * 1000,
            "total_ms": elapsed * 1000,
            "audio_seconds": duration,
            "real_time_factor": elapsed / duration,
            "text": transcript,
        }

    stream = provider.stream([
        {"role": "system", "content": config.dialogue.system_prompt},
        {"role": "user", "content": text},
    ]) if kind == "llm" else provider.synthesize(text)
    first = None
    chunks: list[Any] = []
    try:
        async for chunk in stream:
            if not chunk:
                continue
            if first is None:
                first = time.perf_counter() - started
            chunks.append(chunk)
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
    elapsed = time.perf_counter() - started
    if first is None:
        raise ValueError("provider returned no output")
    result = {"first_response_ms": first * 1000, "total_ms": elapsed * 1000, "chunks": len(chunks)}
    if kind == "llm":
        answer = "".join(chunks)
        result.update(text=answer, characters=len(answer))
    else:
        duration = sum(len(chunk) for chunk in chunks) / (24000 * 2)
        result.update(audio_seconds=duration, real_time_factor=elapsed / duration)
    return result


def failure_hint(kind: str, error: Exception) -> str:
    # Provider exceptions can include authenticated URLs or request bodies.
    if isinstance(error, ModuleNotFoundError):
        return f"缺少 Python 依赖 {error.name}；请安装 requirements.txt 中对应依赖。"
    if isinstance(error, asyncio.TimeoutError):
        return "响应超时；检查服务连通性及 .env 中对应的 VOICE_*_TIMEOUT_S。"
    return {
        "asr": "检查 --audio 文件、VOICE_ASR_MODEL_PATH 下完整的 SenseVoice 模型和 ASR 依赖。",
        "llm": "检查 LLM 服务、VOICE_LLM_BASE_URL、VOICE_LLM_MODEL 和密钥；Ollama 需要先安装模型。",
        "tts": "检查 Edge TTS 网络连通性、edge-tts 依赖及 FFMPEG 可执行文件。",
    }[kind]


async def run_benchmarks(config: AppConfig, *, kinds: list[str], repeat: int, text: str, audio: Path | None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "repeat": repeat,
        "timing_notes": [
            "初始化单独计时；每个模块顺序重复执行，无隐式预热，第一次可能包含冷启动开销。",
            "ASR 为整段识别，首响应等于总耗时；LLM 首响应为第一个非空文本片段，并非精确的 token 计时。",
            "当前 Edge TTS 先下载完整音频再转 PCM；首响应为 provider 首个 PCM 块，包含下载和转码。",
        ],
        "results": [],
    }
    for kind in kinds:
        result: dict[str, Any] = {"module": kind, "ok": False, "runs": []}
        provider = None
        started = time.perf_counter()
        try:
            pcm = await read_audio(audio) if kind == "asr" else b""
            provider_started = time.perf_counter()
            provider = create_provider(kind, config)
            result["provider_load_ms"] = (time.perf_counter() - provider_started) * 1000
            result["setup_ms"] = (time.perf_counter() - started) * 1000
            timeout = getattr(config, kind).timeout_s
            for index in range(repeat):
                run = await asyncio.wait_for(measure(kind, provider, config, text, pcm), timeout)
                result["runs"].append({"run": index + 1, **run})
            result["ok"] = True
            result["mean_first_response_ms"] = statistics.mean(item["first_response_ms"] for item in result["runs"])
            result["mean_total_ms"] = statistics.mean(item["total_ms"] for item in result["runs"])
        except Exception as error:
            result.update(error=type(error).__name__, hint=failure_hint(kind, error))
        finally:
            if kind == "llm" and provider is not None:
                await provider.close()
        report["results"].append(result)
    report["ok"] = all(item["ok"] for item in report["results"])
    return report


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: list[str] | None = None, *, default_config: Path = Path("config.yaml")) -> int:
    parser = argparse.ArgumentParser(description="本地 ASR / LLM / TTS 测速（读取 config.yaml 及同目录 .env）")
    parser.add_argument("module", choices=["asr", "llm", "tts", "all"], help="选择单个模块或全部模块")
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument("--repeat", type=_positive_int, default=3, help="每个模块顺序执行次数，默认 3")
    parser.add_argument("--text", default="你好，请用一句话介绍你自己。", help="LLM 提问及 TTS 合成文本")
    parser.add_argument("--audio", type=Path, help="ASR 输入音频；16 kHz 单声道 PCM WAV 不需要 FFmpeg")
    parser.add_argument("--json", dest="json_output", type=Path, help="JSON 结果路径；使用 - 输出到标准输出")
    args = parser.parse_args(argv)
    if args.module in {"asr", "all"} and args.audio is None:
        parser.error("asr / all 需要 --audio 输入文件")
    kinds = ["asr", "llm", "tts"] if args.module == "all" else [args.module]
    try:
        config = load_config(args.config)
        with redirect_stdout(sys.stderr):
            report = asyncio.run(run_benchmarks(config, kinds=kinds, repeat=args.repeat, text=args.text, audio=args.audio))
    except Exception as error:
        report = {"ok": False, "error": type(error).__name__, "hint": "请检查配置文件路径和 .env 参数。", "results": []}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_output == Path("-"):
        print(payload)
    else:
        for result in report["results"]:
            if result["ok"]:
                print(f"{result['module'].upper()}: 首响应平均 {result['mean_first_response_ms']:.1f} ms，总耗时平均 {result['mean_total_ms']:.1f} ms（{len(result['runs'])} 次）")
            else:
                print(f"{result['module'].upper()}: 失败 ({result['error']})。{result['hint']}", file=sys.stderr)
        if not report["results"]:
            print(f"测速失败 ({report['error']})。{report['hint']}", file=sys.stderr)
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(payload + "\n", encoding="utf-8")
            print(f"JSON: {args.json_output}")
    return 0 if report["ok"] else 1
