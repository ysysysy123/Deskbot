import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deskbot_vision.analyzer import VisionError
from deskbot_vision.camera import capture_camera_image, scan_camera_indexes


class CameraCaptureTest(unittest.TestCase):
    def test_camera_capture_explains_missing_opencv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "camera.jpg"
            with mock.patch.dict(sys.modules, {"cv2": None}):
                with self.assertRaises(VisionError) as context:
                    capture_camera_image(str(output))

        self.assertIn("opencv-python", str(context.exception))

    def test_camera_scan_explains_missing_opencv(self):
        with mock.patch.dict(sys.modules, {"cv2": None}):
            with self.assertRaises(VisionError) as context:
                scan_camera_indexes()

        self.assertIn("opencv-python", str(context.exception))


if __name__ == "__main__":
    unittest.main()
