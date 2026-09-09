import base64
import http.client
import json
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from deskbot_vision.http_server import VisionHttpConfig, create_server


class VisionHttpServerTest(unittest.TestCase):
    def setUp(self):
        self.sample = Path(__file__).resolve().parents[1] / "samples" / "deskbot-scene.ppm"
        self.tmpdir = tempfile.TemporaryDirectory()
        config = VisionHttpConfig(provider="local", upload_dir=self.tmpdir.name)
        self.server = create_server("127.0.0.1", 0, config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmpdir.cleanup()

    def test_health_endpoint(self):
        payload = self._get_json("/health")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["endpoint"], "/mcp/vision/explain")

    def test_explain_accepts_image_path_and_returns_event(self):
        payload = self._post_json(
            "/mcp/vision/explain",
            {
                "request_id": "req-1",
                "device_id": "pc-camera",
                "session_id": "session-1",
                "image_path": str(self.sample),
                "prompt": "Describe the image.",
            },
        )

        self.assertEqual(payload["schema_version"], "deskbot.vision.http.v0")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["event"]["type"], "vision.analysis.completed")
        self.assertEqual(payload["event"]["target"], "dispatcher")
        self.assertEqual(payload["event"]["device_id"], "pc-camera")
        self.assertEqual(payload["result"]["adapter"], "local-static-image-v0")
        self.assertEqual(payload["protocol_event"]["version"], "0.1")
        self.assertEqual(payload["protocol_event"]["type"], "vision.event")
        self.assertEqual(payload["protocol_event"]["correlation_id"], "req-1")
        self.assertEqual(
            payload["protocol_event"]["payload"]["name"], "vision.analysis.completed"
        )

    def test_explain_accepts_base64_image(self):
        encoded = base64.b64encode(self.sample.read_bytes()).decode("ascii")

        payload = self._post_json(
            "/mcp/vision/explain",
            {
                "request_id": "req-2",
                "image_base64": encoded,
                "mime_type": "image/x-portable-pixmap",
            },
        )

        self.assertEqual(payload["status"], "ok")
        self.assertTrue((Path(self.tmpdir.name) / "req-2.ppm").exists())

    def test_explain_accepts_firmware_multipart_upload(self):
        boundary = "DeskbotFirmwareBoundary"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="question"\r\n\r\n',
                b"What is in this image?\r\n",
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="camera.ppm"\r\n',
                b"Content-Type: image/x-portable-pixmap\r\n\r\n",
                self.sample.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        request = urllib.request.Request(
            self.base_url + "/mcp/vision/explain",
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Device-Id": "atk-device-1",
                "Client-Id": "client-1",
            },
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["event"]["device_id"], "atk-device-1")
        self.assertEqual(payload["event"]["session_id"], "client-1")
        self.assertEqual(payload["result"]["prompt"], "What is in this image?")

    def test_explain_accepts_chunked_firmware_multipart_upload(self):
        boundary = "DeskbotChunkedBoundary"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="question"\r\n\r\n',
                b"Describe the image.\r\n",
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="camera.ppm"\r\n',
                b"Content-Type: image/x-portable-pixmap\r\n\r\n",
                self.sample.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=10)
        connection.putrequest("POST", "/mcp/vision/explain")
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.putheader("Device-Id", "atk-device-2")
        connection.endheaders()
        connection.send(f"{len(body):X}\r\n".encode("ascii"))
        connection.send(body)
        connection.send(b"\r\n0\r\n\r\n")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["event"]["device_id"], "atk-device-2")

    def test_explain_rejects_missing_image(self):
        error = self._post_json(
            "/mcp/vision/explain",
            {"request_id": "req-3", "prompt": "Describe the image."},
            expected_status=400,
        )

        self.assertEqual(error["status"], "error")
        self.assertEqual(error["errors"][0]["code"], "missing_image")
        self.assertEqual(error["event"]["type"], "vision.analysis.failed")

    def _get_json(self, path):
        with urllib.request.urlopen(self.base_url + path, timeout=10) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path, payload, expected_status=200):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                self.assertEqual(response.status, expected_status)
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, expected_status)
            return json.loads(exc.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
