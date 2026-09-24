"""A small local MCP bridge for the browser camera.

The camera belongs to the browser, so this bridge keeps the MCP server local and
forwards tool calls to a registered browser tab over a private WebSocket.  It
returns image results for the dialogue backend's separate vision provider.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "self_camera_start",
        "description": "打开浏览器摄像头并返回可用状态。调用拍照前可先调用此工具。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "self_camera_take_photo",
        "description": (
            "使用当前浏览器摄像头拍照并返回 JPEG 图像。用户询问‘拍照’、‘你看到了什么’或"
            "‘描述画面’时调用；question 用于指导视觉模型分析。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "可选的图像分析问题"}
            },
        },
    },
    {
        "name": "self.camera.take_photo",
        "description": "上游小智设备兼容别名：使用当前浏览器摄像头拍照并返回 JPEG 图像。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "可选的图像分析问题"}
            },
        },
    },
    {
        "name": "self_camera_stop",
        "description": "关闭浏览器摄像头并释放设备权限。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "self_camera_status",
        "description": "查询浏览器摄像头是否已经打开。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class CameraMCPBridge:
    """MCP JSON-RPC dispatcher backed by one or more local browser tabs."""

    def __init__(self, *, request_timeout_s: float = 15.0) -> None:
        self.request_timeout_s = request_timeout_s
        self._clients: dict[Any, str | None] = {}
        self._pending: dict[str, tuple[Any, asyncio.Future[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def register(self, websocket: Any) -> None:
        async with self._lock:
            self._clients[websocket] = None

    async def bind(self, websocket: Any, session_id: str | None) -> None:
        async with self._lock:
            if websocket in self._clients:
                self._clients[websocket] = session_id

    async def unregister(self, websocket: Any) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)
            for client, future in self._pending.values():
                if client is websocket and not future.done():
                    future.set_result({"success": False, "error": "摄像头页面已断开连接"})

    async def resolve(self, message: dict[str, Any], websocket: Any) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            return
        async with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending[0] is not websocket:
                return
            _, future = self._pending.pop(request_id)
        if future is not None and not future.done():
            future.set_result(message.get("result") or {"success": False, "error": "摄像头客户端返回空结果"})

    async def _client(self, session_id: str | None = None) -> Any | None:
        async with self._lock:
            clients = [client for client, bound in self._clients.items()
                       if not getattr(client, "closed", False) and (session_id is None or session_id == bound)]
        # Never open the camera on an arbitrary tab when multiple pages exist.
        return clients[0] if len(clients) == 1 else None

    async def call(self, tool_name: str, arguments: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
        client = await self._client(session_id)
        if client is None:
            return {"success": False, "error": "未找到唯一的摄像头页面，请连接本地测试页并指定对应会话"}
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        async with self._lock:
            self._pending[request_id] = (client, future)
        try:
            await client.send_json({
                "type": "camera.mcp.call",
                "id": request_id,
                "tool": tool_name,
                "arguments": arguments,
            })
            return await asyncio.wait_for(future, self.request_timeout_s)
        except asyncio.TimeoutError:
            return {"success": False, "error": "浏览器摄像头响应超时"}
        except Exception as error:
            return {"success": False, "error": f"摄像头客户端不可用：{type(error).__name__}"}
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)

    async def dispatch(self, request: Any) -> dict[str, Any]:
        """Handle one MCP JSON-RPC request and return a JSON-serializable result."""
        if not isinstance(request, dict):
            return self._error(None, -32600, "Invalid Request")
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return self._error(request_id, -32600, "Invalid Request")
        meta = params.get("_meta") or {}
        session_id = meta.get("deskbot/session_id") if isinstance(meta, dict) else None
        if method == "initialize":
            return self._result(request_id, {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "deskbot-camera", "version": "1.0.0"},
            })
        if method == "notifications/initialized":
            return self._result(request_id, {}) if request_id is not None else {}
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            tools = TOOL_DEFINITIONS if session_id is None or await self._client(session_id) is not None else []
            return self._result(request_id, {"tools": tools})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in {item["name"] for item in TOOL_DEFINITIONS} or not isinstance(arguments, dict):
                return self._error(request_id, -32602, "Unknown tool or invalid arguments")
            result = await self.call(name, arguments, session_id)
            return self._tool_result(request_id, result)
        return self._error(request_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def _tool_result(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("success"):
            return self._result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": str(result.get("error", "摄像头工具失败"))}],
                "structuredContent": result,
            })
        content: list[dict[str, Any]] = []
        if result.get("photo_data"):
            image = str(result["photo_data"])
            if image.startswith("data:") and "," in image:
                image = image.split(",", 1)[1]
            try:
                base64.b64decode(image, validate=True)
            except (ValueError, base64.binascii.Error):
                return self._result(request_id, {
                    "isError": True,
                    "content": [{"type": "text", "text": "摄像头返回的图片数据无效"}],
                })
            content.append({"type": "image", "data": image, "mimeType": result.get("mime_type", "image/jpeg")})
        content.append({"type": "text", "text": json.dumps({key: value for key, value in result.items() if key != "photo_data"}, ensure_ascii=False)})
        return self._result(request_id, {"content": content, "structuredContent": {key: value for key, value in result.items() if key != "photo_data"}})
