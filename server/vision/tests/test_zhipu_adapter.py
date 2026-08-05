import json
import unittest
from pathlib import Path
from unittest import mock

from deskbot_vision.config import VisionConfig, load_env_file
from deskbot_vision.zhipu_adapter import ZhipuVisionAnalyzer


class _FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class _FakeResponse:
    headers = _FakeHeaders()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "A small red sample image is visible.",
                        }
                    }
                ]
            }
        ).encode("utf-8")


class ZhipuVisionAnalyzerTest(unittest.TestCase):
    def test_reads_env_file_without_sdk(self):
        env_path = Path(__file__).resolve().parents[1] / ".env.example"
        values = load_env_file(env_path)

        self.assertIn("VISION_PROVIDER", values)
        self.assertEqual(values["VISION_PROVIDER"], "zhipu")

    def test_creates_openai_compatible_request(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "deskbot-scene.ppm"
        analyzer = ZhipuVisionAnalyzer(
            api_key="test-key",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.6v-flash",
            thinking="disabled",
        )

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse()) as urlopen:
            result = analyzer.analyze(str(sample), prompt="What is in this image?")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))

        self.assertEqual(request.full_url, "https://open.bigmodel.cn/api/paas/v4/chat/completions")
        self.assertEqual(payload["model"], "glm-4.6v-flash")
        self.assertEqual(payload["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "image_url")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertTrue(
            payload["messages"][0]["content"][1]["image_url"]["url"].startswith(
                "data:image/x-portable-pixmap;base64,"
            )
        )
        self.assertEqual(result.adapter, "zhipu:glm-4.6v-flash")
        self.assertIn("cloud", result.tags)
        self.assertIn("small red sample", result.summary)

    def test_from_config_requires_api_key(self):
        config = VisionConfig(
            provider="zhipu",
            zhipu_api_key=None,
            zhipu_base_url="https://open.bigmodel.cn/api/paas/v4",
            zhipu_vision_model="glm-4.6v-flash",
            zhipu_thinking=None,
        )

        with self.assertRaises(Exception):
            ZhipuVisionAnalyzer.from_config(config)


if __name__ == "__main__":
    unittest.main()
