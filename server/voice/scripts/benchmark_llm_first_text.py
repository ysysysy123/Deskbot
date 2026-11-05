"""Measure plain LLM first-text latency and observed SDK retries on fixed input."""

import argparse
import asyncio
from contextlib import aclosing
import json
import logging
from pathlib import Path
import sys
import time

from benchmark_voice_segments import distribution


class RetryCounter(logging.Handler):
    def __init__(self):
        super().__init__()
        self.count = 0

    def emit(self, record):
        # Count the installed OpenAI-compatible SDK's retry event without
        # retaining request URLs, headers, keys, or response bodies.
        if str(record.msg).startswith("Retrying request to "):
            self.count += 1


async def run(args):
    from voice_server.config import load_config
    from voice_server.performance import create_provider

    config = load_config(args.config)
    provider = create_provider("llm", config)
    counter = RetryCounter()
    logger = logging.getLogger("openai._base_client")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(counter)
    rows = []
    try:
        for index in range(args.warmup + args.repeat):
            warmup = index < args.warmup
            counter.count = 0
            metrics = {"output_chars": 0}
            started = time.perf_counter()
            try:
                async with asyncio.timeout(config.llm.timeout_s):
                    async with aclosing(provider.stream([
                        {"role": "system", "content": config.dialogue.system_prompt},
                        {"role": "user", "content": args.text},
                    ])) as stream:
                        async for part in stream:
                            if part:
                                metrics.setdefault("first_text_ms", (time.perf_counter() - started) * 1000)
                                metrics["output_chars"] += len(part)
                metrics["status"] = "success" if metrics["output_chars"] else "empty"
            except Exception as error:
                metrics.update(status="error", error_type=type(error).__name__)
            metrics.update(total_ms=(time.perf_counter() - started) * 1000, sdk_retry_events=counter.count)
            metrics = {key: round(value, 2) if isinstance(value, float) else value for key, value in metrics.items()}
            rows.append({"run": index + 1 - args.warmup, "warmup": warmup, "metrics": metrics})
            print(f"{'warmup' if warmup else str(index + 1 - args.warmup)}: {metrics}", file=sys.stderr, flush=True)
    finally:
        logger.removeHandler(counter)
        logger.setLevel(previous_level)
        await provider.close()
    measured = [row["metrics"] for row in rows if not row["warmup"]]
    successful = [row for row in measured if row["status"] == "success"]
    return {
        "ok": len(successful) == args.repeat, "model": config.llm.model,
        "scenario": "sequential plain text LLM requests with one fixed synthetic prompt and no history/tools",
        "text": args.text, "configured_max_retries": config.llm.max_retries,
        "repeat": args.repeat, "warmup": args.warmup,
        "failed_measured": len(measured) - len(successful),
        "summary": {key: distribution([row[key] for row in successful])
                    for key in ("first_text_ms", "total_ms", "sdk_retry_events") if successful},
        "notes": ["Warmups excluded; failures retained in runs and counted separately.",
                  "Retry counts use the installed SDK's INFO retry event, not inference from slow responses.",
                  "No ASR, TTS, tool routing, device playback or production chat history in this scenario."],
        "runs": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="固定合成问题的 LLM 首文字与 SDK 重试观测")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--text", default="请用一句话说明如何整理书桌。")
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("repeat >= 1, warmup >= 0")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    report = asyncio.run(run(args))
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
