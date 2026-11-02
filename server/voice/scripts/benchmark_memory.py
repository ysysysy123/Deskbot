"""Measure local memory writes and retrieval on temporary synthetic history."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

from benchmark_voice_segments import distribution


TOPICS = ("咖啡", "茶饮", "早餐", "运动", "阅读", "音乐", "旅行", "工作", "学习", "电影",
          "甜点", "水果", "蔬菜", "编程", "宠物", "花卉", "作息", "回复", "日程", "项目")


async def run(args):
    from voice_server.memory.sqlite import SQLiteMemoryProvider

    scenarios = []
    with tempfile.TemporaryDirectory(prefix="deskbot-memory-benchmark-") as directory:
        for size in (0, 500, 5000):
            path = Path(directory) / f"history-{size}.db"
            store = SQLiteMemoryProvider(path)
            await store.initialize()
            now = datetime.now(timezone.utc).isoformat()
            # Seed one transaction; setup is outside the measured API calls.
            with sqlite3.connect(path) as connection:
                for index in range(size):
                    topic = TOPICS[(index // 2) % len(TOPICS)]
                    text = (f"记住我的{topic}偏好：我喜欢简单、舒适的安排。" if index < 40 and index % 2 == 0
                            else f"这是第{index + 1}条合成对话，今天聊到{topic}相关的日常安排，后续再讨论具体细节。")
                    connection.execute("INSERT INTO memory_messages VALUES (?,?,?,?,?,?)",
                        (index + 1, "synthetic", "seed", "user" if index % 2 == 0 else "assistant", text, now))
            if size:
                await store.save_summary("synthetic", "此前讨论了日常偏好和安排，具体偏好以长期事实为准。", size - 10)
                for index, topic in enumerate(TOPICS):
                    await store.apply_fact("synthetic", dict(fact_key=f"preference.topic_{index}",
                        content=f"{topic}偏好：喜欢简单、舒适的安排。", category="preference", importance=2,
                        source_message_id=index * 2 + 1, keywords=[topic], action="upsert"))
            for _ in range(3):
                await store.recall_for_turn("synthetic", "我的咖啡偏好是什么？", 10)
            rows = []
            for index in range(args.repeat):
                query = f"我的{TOPICS[index % len(TOPICS)]}偏好是什么？"
                start = time.perf_counter()
                await store.remember("synthetic", "measured", "user", query)
                written = time.perf_counter()
                context = await store.recall_for_turn("synthetic", query, 10)
                recalled = time.perf_counter()
                chars = (len(context.summary) + sum(len(f.content) for f in context.facts)
                         + sum(len(m.content) for m in context.recent_messages + context.relevant_memories))
                rows.append(dict(write_ms=(written - start) * 1000, recall_ms=(recalled - written) * 1000,
                    combined_ms=(recalled - start) * 1000, context_chars=chars,
                    facts=len(context.facts), recent=len(context.recent_messages), relevant=len(context.relevant_memories)))
            scenarios.append(dict(initial_messages=size, initial_facts=20 if size else 0,
                summary={key: distribution([row[key] for row in rows]) for key in rows[0]}, runs=rows))
            await store.close()
    return dict(repeat=args.repeat, scenario="serial local remember + recall_for_turn on synthetic SQLite history",
        notes=["Temporary databases only; no production history, cloud models, WebSocket or speech pipeline.",
               "Three recall warmups per size; each measured turn inserts one user question then retrieves context.",
               "Default 5000-message retention and 8000/900-character context/fact budgets; setup excluded.",
               "Nonempty scenarios start with 20 facts and a summary checkpoint 10 messages behind; text is synthetic."],
        scenarios=scenarios)


def main():
    parser = argparse.ArgumentParser(description="临时合成历史上的本地记忆写入与检索测速，不访问模型")
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 50:
        parser.error("repeat must be between 1 and 50 for this fixed checkpoint scenario")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    report = asyncio.run(run(args))
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: v for k, v in scenario.items() if k != "runs"}
                      for scenario in report["scenarios"]], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
