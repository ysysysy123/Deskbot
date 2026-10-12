from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from voice_server.compat import wait_for


_LOGGER = logging.getLogger(__name__)


class DeviceMcpBridge:
    """Initialize and call the MCP server on one connected physical device."""

    def __init__(
        self, transport: Any, session_id: str, timeout_s: float, *,
        vision: dict[str, str] | None = None, photo_timeout_s: float | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        self._transport = transport
        self._session_id = session_id
        self._timeout_s = max(0.1, timeout_s)
        self._photo_timeout_s = max(self._timeout_s, photo_timeout_s or self._timeout_s)
        self._vision = vision
        self._allowed_tools = allowed_tools
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False
        self._initialization: asyncio.Task[None] | None = None
        self._tools: list[dict[str, Any]] = []
        self._status = "initializing"
        self._error: str | None = None
        self._last_call: dict[str, Any] | None = None
        self._active_motion_calls = 0
        self._motion_needs_stop = False

    def start(self) -> None:
        # Run beside the receive loop, which must remain free to deliver replies.
        if self._initialization is None and not self._closed:
            self._initialization = asyncio.create_task(self._initialize())

    async def _initialize(self) -> None:
        try:
            await self.request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"vision": self._vision} if self._vision else {},
                "clientInfo": {"name": "deskbot-voice", "version": "1.0"},
            })
            await self._transport.send_json({
                "session_id": self._session_id,
                "type": "mcp",
                "payload": {"jsonrpc": "2.0", "method": "notifications/initialized"},
            })
            tools: list[dict[str, Any]] = []
            cursor = ""
            while True:
                params: dict[str, Any] = {"withUserTools": False}
                if cursor:
                    params["cursor"] = cursor
                page = await self.request("tools/list", params)
                if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
                    raise ValueError("设备未返回有效的 MCP 工具列表")
                tools.extend(tool for tool in page["tools"] if isinstance(tool, dict))
                next_cursor = page.get("nextCursor")
                if not next_cursor:
                    break
                if not isinstance(next_cursor, str) or next_cursor == cursor:
                    raise ValueError("设备 MCP 工具列表分页异常")
                cursor = next_cursor
            if self._allowed_tools is None:
                self._tools = tools
            else:
                self._tools = [tool for tool in tools if tool.get("name") in self._allowed_tools]
            self._status = "ready"
            self._error = None
            _LOGGER.info("Device MCP ready: session=%s tools=%d", self._session_id, len(self._tools))
        except Exception as error:
            self._status = "error"
            self._error = str(error) or "设备 MCP 初始化超时，请重新连接设备"
            _LOGGER.warning("Device MCP initialization failed: session=%s error=%s", self._session_id, self._error)

    async def list_tools(self) -> dict[str, Any]:
        self.start()
        if self._closed:
            raise ConnectionError("设备 MCP 会话已关闭")
        if self._initialization is not None:
            await asyncio.shield(self._initialization)
        if self._status != "ready":
            raise ConnectionError(self._error or "设备 MCP 尚未就绪")
        return {"tools": self._tools}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.list_tools()
        call: dict[str, Any] = {"name": name, "status": "running", "started_at": time.time()}
        self._last_call = call
        is_motion = name.startswith("self.motion.") and name != "self.motion.stop"
        if is_motion:
            self._active_motion_calls += 1
        try:
            if not any(tool.get("name") == name for tool in self._tools):
                raise ValueError("当前设备未提供此工具")
            result = await self.request(
                "tools/call", {"name": name, "arguments": arguments},
                timeout_s=self._photo_timeout_s if name == "self.camera.take_photo" else None,
            )
            if not isinstance(result, dict):
                raise ValueError("设备 MCP 工具返回格式无效")
            structured = result.get("structuredContent") or {}
            contents = result.get("content", [])
            if len(contents) == 1 and contents[0].get("type") == "text":
                try:
                    value = json.loads(contents[0]["text"])
                except (ValueError, KeyError):
                    value = None
                if value is False or (isinstance(value, dict) and value.get("success") is False):
                    result["isError"] = True
            if structured.get("success") is False:
                result["isError"] = True
            summary = "\n".join(
                item["text"] for item in result.get("content", [])
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
            )[:300]
            call["summary"] = summary
            if result.get("isError"):
                call.update(status="error", error=summary or "设备工具执行失败")
                self._error = call["error"]
            else:
                call["status"] = "success"
                self._error = None
            _LOGGER.info(
                "Device MCP call: session=%s name=%s status=%s",
                self._session_id, name, call["status"],
            )
            return result
        except Exception as error:
            self._error = str(error) or "设备工具响应超时"
            call.update(status="error", error=self._error)
            raise
        except asyncio.CancelledError:
            if is_motion:
                self._motion_needs_stop = True
            call.update(status="error", error="工具调用已取消")
            raise
        finally:
            if is_motion:
                self._active_motion_calls = max(0, self._active_motion_calls - 1)
            call["finished_at"] = time.time()

    def snapshot(self) -> dict[str, Any]:
        return {
            "supported": True,
            "status": self._status,
            "tools": [{"name": tool.get("name", ""), "description": tool.get("description", "")} for tool in self._tools],
            "error": self._error,
            "last_call": dict(self._last_call) if self._last_call else None,
        }

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout_s: float | None = None,
    ) -> Any:
        if self._closed:
            raise ConnectionError("设备 MCP 会话已关闭")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._transport.send_json({"session_id": self._session_id, "type": "mcp", "payload": payload})
            response = await wait_for(future, timeout_s or self._timeout_s)
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            error = response.get("error")
            message = error.get("message", "设备 MCP 调用失败") if isinstance(error, dict) else "设备 MCP 调用失败"
            raise ValueError(str(message))
        return response.get("result", {})

    def resolve(self, payload: dict[str, Any]) -> bool:
        request_id = payload.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, int):
            return False
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True

    async def close(self) -> None:
        self._closed = True
        self._status = "closed"
        if self._initialization is not None and not self._initialization.done():
            self._initialization.cancel()
            await asyncio.gather(self._initialization, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("设备 MCP 会话已关闭"))
        self._pending.clear()

    async def stop_active_motion(self) -> None:
        """Best-effort stop for a motion command that was cancelled locally."""
        if self._closed or not self._motion_needs_stop:
            return
        if not any(tool.get("name") == "self.motion.stop" for tool in self._tools):
            return
        try:
            await self.request("tools/call", {"name": "self.motion.stop", "arguments": {}}, timeout_s=1.0)
            self._motion_needs_stop = False
        except (asyncio.CancelledError, ConnectionError, TimeoutError, ValueError):
            _LOGGER.debug("Unable to stop device motion after cancellation: session=%s", self._session_id)
