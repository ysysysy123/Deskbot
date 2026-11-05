"""Summarize device-clock Voice timing events without mixing server clocks."""

import argparse
import json
from pathlib import Path
import re

from benchmark_voice_segments import distribution


def summarize(text):
    turns = {}
    for line in text.splitlines():
        if "Voice timing: " not in line:
            continue
        payload = line.split("Voice timing: ", 1)[1]
        payload = re.sub(r"\x1b\[[0-9;]*m", "", payload)
        fields = dict(re.findall(r"(\w+)=([^\s]+)", payload))
        turn = turns.setdefault(fields["turn"], {"turn_id": fields["turn"]})
        event = fields.pop("event")
        fields.pop("turn")
        turn[event] = {key: int(value) for key, value in fields.items()}
    rows = []
    for turn in turns.values():
        row = {"turn_id": turn["turn_id"], "events": {key: value for key, value in turn.items() if key != "turn_id"}}
        output, drained, barge = turn.get("first_output", {}), turn.get("drained", {}), turn.get("barge_in", {})
        for key, start, end in (
            ("eou_to_output_ms", output.get("voice_end_us"), output.get("output_us")),
            ("first_audio_to_output_ms", output.get("received_us"), output.get("output_us")),
            ("output_to_last_drain_ms", output.get("output_us"), drained.get("drained_us")),
            ("vad_to_queue_reset_ms", barge.get("detected_us"), barge.get("reset_us")),
        ):
            if start is not None and end is not None and 0 < start <= end:
                row[key] = round((end - start) / 1000, 2)
        rows.append(row)
    keys = ("eou_to_output_ms", "first_audio_to_output_ms", "output_to_last_drain_ms", "vad_to_queue_reset_ms")
    return {
        "turn_count": len(rows),
        "summary_ms": {key: distribution([row[key] for row in rows if key in row])
                       for key in keys if any(key in row for row in rows)},
        "notes": ["All intervals use timestamps emitted by one device event/turn on its monotonic clock.",
                  "Missing or unavailable anchors are omitted; each metric reports its own sample count.",
                  "Output marks a codec OutputData call; queue reset/drain are software milestones, not acoustic measurements.",
                  "Last drain means the last observed queue-empty event; underflow can also drain a queue before a reply ends.",
                  "No server timestamps, chat text, credentials or unrecognized log lines are copied into this report."],
        "turns": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="只读汇总 ESP32 串口日志中的设备时钟语音计时")
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.log.read_text(encoding="utf-8", errors="replace"))
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary_ms"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
