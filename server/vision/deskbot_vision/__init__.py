"""Deskbot vision service package."""

from .analyzer import StaticImageAnalyzer, VisionError
from .zhipu_adapter import ZhipuVisionAnalyzer

__all__ = ["StaticImageAnalyzer", "VisionError", "ZhipuVisionAnalyzer"]
