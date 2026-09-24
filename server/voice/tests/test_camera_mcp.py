import asyncio
import base64

from voice_server.camera_mcp import CameraMCPBridge


class FakeCameraClient:
    closed = False

    def __init__(self, bridge):
        self.bridge = bridge
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)
        await self.bridge.resolve({
            "type": "camera.mcp.result",
            "id": message["id"],
            "result": {"success": True, "active": True},
        }, self)


def test_camera_tools_are_registered_without_a_browser():
    async def run():
        bridge = CameraMCPBridge()
        listed = await bridge.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [item["name"] for item in listed["result"]["tools"]]
        assert names == [
            "self_camera_start",
            "self_camera_take_photo",
            "self.camera.take_photo",
            "self_camera_stop",
            "self_camera_status",
        ]
        result = await bridge.dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "self_camera_status", "arguments": {}},
        })
        assert result["result"]["isError"] is True

    asyncio.run(run())


def test_camera_tool_call_round_trips_to_registered_browser():
    async def run():
        bridge = CameraMCPBridge(request_timeout_s=1)
        client = FakeCameraClient(bridge)
        await bridge.register(client)
        result = await bridge.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "self_camera_start", "arguments": {}},
        })
        assert result["result"]["structuredContent"]["active"] is True
        assert client.messages[0]["tool"] == "self_camera_start"

    asyncio.run(run())


def test_camera_photo_result_contains_mcp_image_content():
    bridge = CameraMCPBridge()
    data = base64.b64encode(b"jpeg").decode()
    result = bridge._tool_result(4, {
        "success": True, "photo_data": data, "mime_type": "image/jpeg",
        "photo_width": 10, "photo_height": 10,
    })
    assert result["result"]["content"][0] == {"type": "image", "data": data, "mimeType": "image/jpeg"}
    assert "photo_data" not in result["result"]["structuredContent"]


def test_camera_calls_use_the_voice_session_with_multiple_tabs():
    async def run():
        bridge = CameraMCPBridge(request_timeout_s=1)
        first, second = FakeCameraClient(bridge), FakeCameraClient(bridge)
        await bridge.register(first)
        await bridge.register(second)
        await bridge.bind(first, "session-a")
        await bridge.bind(second, "session-b")
        result = await bridge.dispatch({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                "name": "self_camera_start", "arguments": {},
                "_meta": {"deskbot/session_id": "session-b"},
            },
        })
        assert result["result"]["structuredContent"]["success"] is True
        assert not first.messages
        assert len(second.messages) == 1
        ambiguous = await bridge.call("self_camera_start", {})
        assert ambiguous["success"] is False
        missing = await bridge.dispatch({
            "jsonrpc": "2.0", "id": 6, "method": "tools/list",
            "params": {"_meta": {"deskbot/session_id": "missing"}},
        })
        assert missing["result"]["tools"] == []

    asyncio.run(run())


def test_camera_disconnect_finishes_pending_call_and_other_tab_cannot_resolve():
    async def run():
        bridge = CameraMCPBridge(request_timeout_s=1)
        entered = asyncio.Event()

        class PendingCamera:
            closed = False

            async def send_json(self, message):
                self.message = message
                entered.set()

        client = PendingCamera()
        other = FakeCameraClient(bridge)
        await bridge.register(client)
        await bridge.bind(client, "active")
        task = asyncio.create_task(bridge.call("self_camera_start", {}, "active"))
        await entered.wait()
        await bridge.resolve({"id": client.message["id"], "result": {"success": True}}, other)
        assert not task.done()
        await bridge.unregister(client)
        assert (await task)["success"] is False

    asyncio.run(run())
