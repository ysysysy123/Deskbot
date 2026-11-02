"""Exercise real-model memory updates using synthetic messages and a temporary DB."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time


async def run(config):
    from voice_server.memory.service import MemoryService
    from voice_server.memory.sqlite import SQLiteMemoryProvider
    from voice_server.performance import create_provider
    from voice_server.session import VoiceSession

    provider = create_provider("llm", config)
    class RecordingProvider:
        def __init__(self):
            self.outputs = []

        async def stream(self, messages, *, max_tokens=None):
            parts = []
            try:
                async for chunk in provider.stream(messages, max_tokens=max_tokens):
                    parts.append(chunk)
                    yield chunk
            finally:
                self.outputs.append("".join(parts))

    recorder = RecordingProvider()
    report = {"model": config.llm.model, "temporary_database": True, "cases": []}
    try:
        with tempfile.TemporaryDirectory(prefix="deskbot-memory-check-") as directory:
            path = Path(directory) / "memory.db"
            store = SQLiteMemoryProvider(path)
            await store.initialize()
            service = MemoryService(store, store, recorder, 12,
                                    summary_max_tokens=config.memory.consolidation_max_tokens,
                                    idle_delay_s=config.memory.idle_delay_s)
            source_ids = []
            try:
                for index, text in enumerate(("记住我的咖啡偏好：我喜欢拿铁咖啡。",
                                              "记住我现在咖啡改喝美式了，不喝拿铁了。",
                                              "忘掉我的咖啡偏好。")):
                    source_ids.append(await service.remember("synthetic-device", f"s{index}", "user", text))
                    started = time.monotonic()
                    reply = await service.process_command("synthetic-device", text)
                    elapsed = round((time.monotonic() - started) * 1000, 1)
                    context = await service.recall_for_turn("synthetic-device", "咖啡偏好", 10)
                    contents = [fact.content for fact in context.facts]
                    # Assemble the real production prompt for a different session.
                    prompt_session = VoiceSession.__new__(VoiceSession)
                    prompt_session._system_prompt = config.dialogue.system_prompt
                    prompt_session.session_id = f"reconnected-{index}"
                    question = "我喜欢哪种咖啡？只根据已有记忆回答，没记录就说不知道。"
                    answer_started = time.monotonic()
                    answer = "".join([chunk async for chunk in provider.stream(prompt_session._make_messages(context, question))])
                    answer_ms = round((time.monotonic() - answer_started) * 1000, 1)
                    if index == 0:
                        passed = len(contents) == 1 and "拿铁" in contents[0] and "拿铁" in answer
                    elif index == 1:
                        passed = len(contents) == 1 and "美式" in contents[0] and "美式" in answer
                    else:
                        visible = {message.id for message in context.recent_messages + context.relevant_memories}
                        passed = not contents and not ({*source_ids[:2]} & visible) and "拿铁" not in context.summary and "拿铁" not in answer and "美式" not in answer
                    report["cases"].append({"input": text, "reply": reply, "elapsed_ms": elapsed,
                                            "facts": contents, "recall_answer": answer, "answer_ms": answer_ms, "passed": passed})
                    if not passed:
                        report["cases"][-1]["synthetic_model_output"] = recorder.outputs[-1]
                    await service.remember("synthetic-device", f"s{index}", "assistant", reply or "")
                reopened = SQLiteMemoryProvider(path)
                await reopened.initialize()
                report["forget_persists_after_reopen"] = not (await reopened.recall("synthetic-device", "咖啡", 10)).facts
            finally:
                await service.close()
    finally:
        await provider.close()
    report["ok"] = all(case["passed"] for case in report["cases"]) and report["forget_persists_after_reopen"]
    return report


def main():
    parser = argparse.ArgumentParser(description="用临时数据库和合成内容验证真实 LLM 记忆更新，不访问真实聊天历史")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--json", type=Path, help="保存结果 JSON")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from voice_server.config import load_config

    try:
        report = asyncio.run(run(load_config(args.config)))
    except Exception as error:
        report = {"ok": False, "error": type(error).__name__}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
