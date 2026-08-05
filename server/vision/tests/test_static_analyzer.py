import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deskbot_vision import StaticImageAnalyzer, VisionError


class StaticImageAnalyzerTest(unittest.TestCase):
    def test_analyzes_sample_ppm(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "deskbot-scene.ppm"

        result = StaticImageAnalyzer().analyze(str(sample))

        self.assertEqual(result.image.format, "PPM")
        self.assertEqual(result.image.width, 4)
        self.assertEqual(result.image.height, 3)
        self.assertIn("red", result.tags)
        self.assertEqual(result.errors, [])

    def test_rejects_missing_file(self):
        with self.assertRaises(VisionError):
            StaticImageAnalyzer().analyze("missing-image.ppm")

    def test_cli_outputs_json(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "deskbot-scene.ppm"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "deskbot_vision.cli",
                str(sample),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema_version"], "deskbot.vision.result.v0")
        self.assertEqual(payload["image"]["width"], 4)

    def test_cli_writes_output_file(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "deskbot-scene.ppm"
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "vision.json"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "deskbot_vision.cli",
                    str(sample),
                    "--output",
                    str(output),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=True,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["adapter"], "local-static-image-v0")


if __name__ == "__main__":
    unittest.main()
