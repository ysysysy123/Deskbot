"""PC camera capture helpers for the Deskbot vision service."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .analyzer import VisionError, detect_image_info


def capture_camera_image(
    output_path: str,
    camera_index: int = 0,
    warmup_frames: int = 5,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Path:
    """Capture one frame from a PC camera and write it as an image file."""

    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise VisionError(
            "PC camera capture requires opencv-python. "
            "Install it in the active environment with: python -m pip install opencv-python"
        ) from exc
    _quiet_opencv_logs(cv2)

    output = Path(output_path)
    if not output.suffix:
        output = output.with_suffix(".jpg")
    output.parent.mkdir(parents=True, exist_ok=True)

    capture = _open_capture(cv2, camera_index)
    if not capture or not capture.isOpened():
        if capture:
            capture.release()
        raise VisionError(f"Could not open PC camera index {camera_index}.")

    try:
        if width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        frame = None
        frames_to_read = max(1, warmup_frames + 1)
        for _ in range(frames_to_read):
            ok, candidate = capture.read()
            if ok and candidate is not None:
                frame = candidate

        if frame is None:
            raise VisionError(f"PC camera index {camera_index} did not return a frame.")

        if not cv2.imwrite(str(output), frame):
            raise VisionError(f"Could not write captured frame: {output}")
    finally:
        capture.release()

    detect_image_info(output.read_bytes())
    return output


def scan_camera_indexes(max_index: int = 5) -> List[Dict[str, object]]:
    """Return a lightweight availability report for camera indexes."""

    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise VisionError(
            "PC camera scan requires opencv-python. "
            "Install it in the active environment with: python -m pip install opencv-python"
        ) from exc
    _quiet_opencv_logs(cv2)

    reports: List[Dict[str, object]] = []
    for camera_index in range(max(0, max_index) + 1):
        available_backends: List[str] = []
        for backend_name in ["CAP_DSHOW", "CAP_MSMF", "CAP_ANY"]:
            backend = getattr(cv2, backend_name, None)
            if backend is None:
                continue
            capture = cv2.VideoCapture(camera_index, backend)
            opened = bool(capture and capture.isOpened())
            if opened:
                available_backends.append(backend_name)
            if capture:
                capture.release()

        reports.append(
            {
                "camera_index": camera_index,
                "available": bool(available_backends),
                "backends": available_backends,
            }
        )
    return reports


def _open_capture(cv2, camera_index: int):
    backend_names = ["CAP_DSHOW", "CAP_MSMF", "CAP_ANY"]
    for backend_name in backend_names:
        backend = getattr(cv2, backend_name, None)
        if backend is None:
            continue
        capture = cv2.VideoCapture(camera_index, backend)
        if capture and capture.isOpened():
            return capture
        if capture:
            capture.release()

    return cv2.VideoCapture(camera_index)


def _quiet_opencv_logs(cv2) -> None:
    set_log_level = getattr(cv2, "setLogLevel", None)
    if set_log_level is None:
        return
    try:
        set_log_level(0)
    except Exception:
        return
