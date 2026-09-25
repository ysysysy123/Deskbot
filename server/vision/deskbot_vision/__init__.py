"""Deskbot vision service package."""

from .analyzer import StaticImageAnalyzer, VisionError
from .camera import capture_camera_image, scan_camera_indexes
from .zhipu_adapter import ZhipuVisionAnalyzer

__all__ = [
    "StaticImageAnalyzer",
    "VisionError",
    "ZhipuVisionAnalyzer",
    "capture_camera_image",
    "scan_camera_indexes",
]
