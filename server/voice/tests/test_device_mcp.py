import asyncio

from voice_server.device_mcp import DeviceMcpBridge


async def test_cancelled_motion_can_be_stopped_after_request_is_cancelled():
    gate = asyncio.Event()
    stopped = asyncio.Event()

    class Bridge(DeviceMcpBridge):
        async def list_tools(self):
            self._tools = [{"name": "self.motion.drive"}, {"name": "self.motion.stop"}]
            return {"tools": self._tools}

        async def request(self, method, params=None, *, timeout_s=None):
            if params and params.get("name") == "self.motion.stop":
                stopped.set()
                return {"content": [{"type": "text", "text": "true"}]}
            await gate.wait()
            return {"content": [{"type": "text", "text": "true"}]}

    bridge = Bridge(object(), "session", 1)
    task = asyncio.create_task(bridge.call_tool("self.motion.drive", {"duration_ms": 1000}))
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await bridge.stop_active_motion()
    assert stopped.is_set()
