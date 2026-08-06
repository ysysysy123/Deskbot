"""Configuration helpers for the vision service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class VisionConfig:
    provider: str
    zhipu_api_key: Optional[str]
    zhipu_base_url: str
    zhipu_vision_model: str
    zhipu_thinking: Optional[str]


def load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_config(env_path: Optional[str] = None) -> VisionConfig:
    env_file = Path(env_path) if env_path else Path.cwd() / ".env"
    file_values = load_env_file(env_file)

    def get(name: str, default: Optional[str] = None) -> Optional[str]:
        return os.environ.get(name) or file_values.get(name) or default

    return VisionConfig(
        provider=(get("VISION_PROVIDER", "local") or "local").lower(),
        zhipu_api_key=get("ZHIPUAI_API_KEY"),
        zhipu_base_url=get("ZHIPUAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
        or "https://open.bigmodel.cn/api/paas/v4",
        zhipu_vision_model=get("ZHIPUAI_VISION_MODEL", "glm-4.6v-flash") or "glm-4.6v-flash",
        zhipu_thinking=get("ZHIPUAI_THINKING"),
    )
