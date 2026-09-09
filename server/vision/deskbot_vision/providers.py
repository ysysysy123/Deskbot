"""Provider selection helpers for Deskbot vision analyzers."""

from __future__ import annotations

from .analyzer import StaticImageAnalyzer, VisionError
from .config import load_config
from .zhipu_adapter import ZhipuVisionAnalyzer


def build_analyzer(provider: str, env_file: str | None, retries: int = 0, retry_delay: float = 5.0):
    if provider == "local":
        return StaticImageAnalyzer()

    config = load_config(env_file)
    selected_provider = config.provider if provider == "auto" else provider
    if selected_provider == "local":
        return StaticImageAnalyzer()
    if selected_provider == "zhipu":
        return ZhipuVisionAnalyzer.from_config(config, retries=retries, retry_delay_seconds=retry_delay)
    raise VisionError(f"Unsupported vision provider: {selected_provider}")
