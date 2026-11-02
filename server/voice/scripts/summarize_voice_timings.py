"""Summarize existing voice turn logs without starting models or services."""

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics


METRICS = ("asr_ms", "memory_ms", "first_text_ms", "first_sentence_ms", "first_tts_request_ms", "first_pcm_ms", "first_audio_ms", "output_audio_ms", "total_ms")


def summarize(rows):
    result = {}
    for key in (*METRICS, "text_to_audio_ms", "text_to_sentence_ms", "sentence_to_tts_ms", "tts_to_pcm_ms", "pcm_to_audio_ms"):
        values = sorted(row[key] for row in rows if key in row)
        if not values:
            continue
        position = (len(values) - 1) * 0.95
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        result[key] = {
            "n": len(values),
            "p50": round(statistics.median(values), 1),
            "p95": round(values[lower] + (values[upper] - values[lower]) * (position - lower), 1),
            "min": min(values),
            "max": max(values),
        }
    return {"turns": len(rows), "metrics_ms": result}


def read_turns(path):
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if "Voice turn timings:" not in line or "metrics=" not in line:
            continue
        record = ast.literal_eval(line.split("metrics=", 1)[1])
        row = {key: record[key] for key in METRICS if key in record}
        row.update({key: record[key] for key in ("tts_requests", "pcm_bytes", "audio_packets", "audio_bytes") if key in record})
        row.update(line=line_number, status=record["status"], has_tool_calls=bool(record.get("tool_calls")))
        if "error_stage" in record:
            row["error_stage"] = record["error_stage"]
        if "first_audio_ms" in row and "first_text_ms" in row:
            row["text_to_audio_ms"] = round(row["first_audio_ms"] - row["first_text_ms"], 1)
        for start, end, name in (("first_text_ms", "first_sentence_ms", "text_to_sentence_ms"),
                                 ("first_sentence_ms", "first_tts_request_ms", "sentence_to_tts_ms"),
                                 ("first_tts_request_ms", "first_pcm_ms", "tts_to_pcm_ms"),
                                 ("first_pcm_ms", "first_audio_ms", "pcm_to_audio_ms")):
            if start in row and end in row:
                row[name] = round(row[end] - row[start], 1)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="只读汇总 voice.log 中的历史回合计时")
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path, help="保存 JSON；省略时输出到标准输出")
    args = parser.parse_args()
    rows = read_turns(args.log)
    successful = [row for row in rows if row["status"] == "success" and "first_audio_ms" in row]
    voice = [row for row in successful if row.get("asr_ms", 0) > 0]
    groups = {
        "successful_voice_with_audio": voice,
        "successful_voice_without_tool_records": [row for row in voice if not row["has_tool_calls"]],
        "successful_voice_with_tool_records": [row for row in voice if row["has_tool_calls"]],
        "successful_text_with_audio": [row for row in successful if row.get("asr_ms") == 0],
    }
    report = {
        "source": str(args.log.resolve()),
        "source_modified_utc": datetime.fromtimestamp(args.log.stat().st_mtime, timezone.utc).isoformat(),
        "notes": [
            "历史调试日志，混合版本、输入长度和浏览器/设备路径；不是当前版本的受控性能测试。",
            "仅对成功且发出音频的回合计算响应分位数；空识别、错误和取消不计入成功响应分布。",
            "语音按 asr_ms > 0 区分；工具按有无 tool_calls 记录区分，不代表已核实请求语义。",
            "首文字、首音频、总耗时均从服务端 pipeline 启动累计；不含此前录音和 VAD 静音等待。",
            "首音频是服务端首个音频包发送完成；总耗时是 pipeline 结束，不是设备播放完成。",
            "text_to_audio_ms 包含成句、排队、TTS、转码与发包，不能视为纯 TTS 耗时。",
            "p95 使用排序后 (n-1)*0.95 位置线性插值。每个字段单独统计实际存在的样本。",
        ],
        "turn_count": len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "error_stage_counts": dict(Counter(row.get("error_stage", "unknown") for row in rows if row["status"] == "error")),
        "groups": {name: summarize(group) for name, group in groups.items()},
        "turns": rows,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
        print(f"{len(rows)} turns -> {args.output}")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
