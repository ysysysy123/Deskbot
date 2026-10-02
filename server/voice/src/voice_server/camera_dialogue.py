"""A bounded MCP tool loop behind the existing voice/Agent dialogue boundary."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

import aiohttp

from voice_server.dialogue import TurnRequest
from voice_server.providers.openai_compatible import OpenAICompatibleLLMProvider, ToolCall
from voice_server.providers.openai_vision import OpenAICompatibleVisionProvider


_CAMERA_TOOLS = {
    "self_camera_start", "self_camera_take_photo", "self_camera_stop", "self_camera_status",
}
_DEVICE_TOOL_PREFIXES = ("self.motion.", "self.sensor.", "self.camera.")
_CAMERA_SWITCH_COMMANDS = (
    re.compile(r"(?:(?:请(?:帮我)?|帮我|麻烦(?:你)?|可以)\s*)?(打开|开启|关闭|关掉)\s*(?:摄像头|相机)(?:一下|吗)?[。.!！?？]?"),
    re.compile(r"(?:请)?(?:帮我)?把\s*(?:摄像头|相机)\s*(打开|开启|关闭|关掉)(?:一下)?[。.!！]?"),
)
_TOOL_INSTRUCTIONS = (
    "你可以通过工具操作当前对话浏览器的摄像头。用户要求打开、关闭、拍照或观察当前画面时，"
    "调用相应工具；观察画面直接调用拍照工具即可，无需先查询状态。"
    "工具返回结果之前，不能说操作已经成功。只根据工具结果说明成功或失败；"
    "不能编造画面。拍照成功后，用 vision_description 中的视觉分析回答用户的问题，"
    "不要复述拍照参数 question 或把浏览器的状态消息当作画面描述。"
    "拍照结果没有视觉描述时，说明已拍照但尚不能解读图像。"
)
_DEVICE_TOOL_INSTRUCTIONS = (
    "你还可以通过工具控制当前语音连接的实机。只有用户明确要求时才执行运动；"
    "运动必须使用短时长 duration_ms，范围不超过 5000 毫秒，不要编造执行结果。"
    "self.motion.stop 可以随时执行；读取 self.sensor.get_imu 只在用户询问姿态或传感器时调用。"
)


def _direct_camera_tool(text: str) -> str | None:
    """Execute clear device requests even if the chat model omits its tool call."""
    text = text.strip()
    # Questions about using the camera and negative instructions are discussion,
    # not permission to capture a new image.
    if re.search(r"不要|不用|不需要|无需|别(?:打开|开启|拍|看)", text):
        return None
    if re.match(r"(?:请问)?(?:如何|怎样|怎么)", text):
        return None
    for pattern in _CAMERA_SWITCH_COMMANDS:
        command = pattern.fullmatch(text)
        if command is not None:
            return "self_camera_start" if command[1] in {"打开", "开启"} else "self_camera_stop"
    photo_request = re.search(r"拍(?:一张|张|个)?照|拍摄", text)
    observation = re.search(r"看看|看一下|看一眼|看到|看见|观察|识别|描述", text)
    visible_subject = re.search(r"摄像头|相机|画面|镜头|手[里中]|桌上|面前|眼前", text)
    current_view_question = re.fullmatch(
        r"(?:你)?(?:现在)?(?:能)?(?:看到|看见)了?(?:什么|啥)(?:东西)?[。.!！?？]?", text,
    )
    if photo_request or current_view_question or (observation and visible_subject):
        return "self_camera_take_photo"
    return None


def _device_tool_name(tool_name: str, available_names: set[str]) -> str | None:
    if tool_name in available_names:
        return tool_name
    if tool_name == "self_camera_take_photo" and "self.camera.take_photo" in available_names:
        return "self.camera.take_photo"
    if tool_name == "self_camera_start" and "self.camera.start" in available_names:
        return "self.camera.start"
    if tool_name == "self_camera_stop" and "self.camera.stop" in available_names:
        return "self.camera.stop"
    return None


def _direct_stop_tool(text: str, available_names: set[str]) -> str | None:
    if "self.motion.stop" not in available_names:
        return None
    if re.fullmatch(r"(?:请)?(?:马上|立即|赶紧)?(?:停下|停止|停住|刹车)(?:一下)?[。.!！?？]?", text.strip()):
        return "self.motion.stop"
    return None


class CameraMcpDialogueBackend:
    def __init__(
        self, llm: OpenAICompatibleLLMProvider, *, mcp_url: str,
        mcp_timeout_s: float = 20.0, max_tool_rounds: int = 3,
        vision: OpenAICompatibleVisionProvider | None = None,
    ) -> None:
        self._llm = llm
        self._mcp_url = mcp_url
        self._timeout = aiohttp.ClientTimeout(total=mcp_timeout_s)
        self._max_tool_rounds = max_tool_rounds
        self._vision = vision

    async def close(self) -> None:
        if self._vision is not None:
            await self._vision.close()

    async def _rpc(
        self, client: aiohttp.ClientSession, session_id: str, method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(params or {})
        payload["_meta"] = {"deskbot/session_id": session_id}
        async with client.post(self._mcp_url, json={
            "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": payload,
        }) as response:
            response.raise_for_status()
            body = await response.json()
        if "error" in body:
            raise ValueError(str(body["error"].get("message", "MCP 请求失败")))
        return body["result"]

    async def stream(self, request: TurnRequest) -> AsyncIterator[str]:
        messages: list[dict[str, Any]] = [dict(message) for message in request.messages]
        async with aiohttp.ClientSession(timeout=self._timeout) as client:
            browser_tools: list[dict[str, Any]] = []
            try:
                listed = await self._rpc(client, request.session_id, "tools/list")
                browser_tools = [{"type": "function", "function": {
                    "name": tool["name"], "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                }} for tool in listed.get("tools", []) if tool.get("name") in _CAMERA_TOOLS]
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError):
                browser_tools = []
            device_tools: list[dict[str, Any]] = []
            device_tool_names: set[str] = set()
            if request.device_mcp is not None:
                try:
                    device_list = await request.device_mcp.list_tools()
                    device_tools = [{"type": "function", "function": {
                        "name": tool["name"], "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    }} for tool in device_list.get("tools", [])
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str)
                    and tool["name"].startswith(_DEVICE_TOOL_PREFIXES)]
                    device_tool_names = {tool["function"]["name"] for tool in device_tools}
                except (asyncio.TimeoutError, ConnectionError, ValueError, KeyError, AttributeError):
                    device_tools = []
            tools = browser_tools + device_tools
            if browser_tools and device_tools:
                instructions = _TOOL_INSTRUCTIONS + " " + _DEVICE_TOOL_INSTRUCTIONS
            elif browser_tools:
                instructions = _TOOL_INSTRUCTIONS
            elif device_tools:
                instructions = _DEVICE_TOOL_INSTRUCTIONS
            else:
                instructions = (
                    "当前对话没有可用的设备工具。不要声称已操作摄像头、底盘或传感器；"
                    "用户要求操作设备时，请说明设备 MCP 尚未连接。"
                )
            # GLM can ignore earlier system messages. Keep the dialogue prompt,
            # memory summary and current tool instructions in one system message.
            system_parts = []
            while messages and messages[0]["role"] == "system":
                system_parts.append(messages.pop(0)["content"])
            system_parts.append(instructions)
            messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
            available_names = {tool["function"]["name"] for tool in tools}
            direct_stop = _direct_stop_tool(request.text, available_names)
            if direct_stop is not None:
                result = json.loads(await self._execute(
                    client, request, ToolCall(uuid.uuid4().hex, direct_stop, "{}"),
                    available_names, device_tool_names,
                ))
                if result.get("success") is True:
                    yield "设备已停止。"
                else:
                    reason = result.get("error") or result.get("tool_message") or "设备没有确认停止"
                    yield f"设备停止失败：{reason}"
                return
            direct_camera = _direct_camera_tool(request.text)
            tool_name = (_device_tool_name(direct_camera, available_names) or direct_camera) if direct_camera else None
            if tool_name is not None:
                arguments = {"question": request.text} if direct_camera == "self_camera_take_photo" else {}
                result = json.loads(await self._execute(
                    client, request, ToolCall(uuid.uuid4().hex, tool_name, self._text(arguments)),
                    available_names, device_tool_names,
                ))
                if direct_camera == "self_camera_take_photo":
                    # This is the actual image analysis; another text-model
                    # round can replace it with an ungrounded confirmation.
                    if result.get("photo_captured") and result.get("vision_description"):
                        yield result["vision_description"]
                    else:
                        reason = result.get("error") or result.get("tool_message") or "工具没有返回可分析的照片"
                        yield f"无法查看摄像头画面：{reason}"
                else:
                    action = "打开" if tool_name == "self_camera_start" else "关闭"
                    if result.get("success") is True:
                        yield f"摄像头已{action}。"
                    else:
                        reason = result.get("error") or result.get("tool_message") or "工具未确认操作成功"
                        yield f"摄像头{action}失败：{reason}"
                return
            for round_index in range(self._max_tool_rounds + 1):
                calls: list[ToolCall] = []
                spoken: list[str] = []
                allowed_tools = tools if round_index < self._max_tool_rounds else []
                async with aclosing(self._llm.stream_with_tools(messages, allowed_tools)) as stream:
                    async for item in stream:
                        if isinstance(item, ToolCall):
                            calls.append(item)
                        else:
                            spoken.append(item)
                            yield item
                if not calls:
                    return
                if not allowed_tools:
                    yield "本轮设备操作已达到次数上限，请再试一次。"
                    return
                messages.append({
                    "role": "assistant", "content": "".join(spoken) or None,
                    "tool_calls": [{"id": call.id, "type": "function", "function": {
                        "name": call.name, "arguments": call.arguments,
                    }} for call in calls],
                })
                for call in calls:
                    result = await self._execute(client, request, call, available_names, device_tool_names)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    async def _execute(
        self, client: aiohttp.ClientSession, request: TurnRequest, call: ToolCall,
        available_names: set[str], device_tool_names: set[str],
    ) -> str:
        try:
            if call.name not in available_names:
                error = "当前会话没有此摄像头工具" if call.name.startswith("self_camera_") else "当前会话没有此设备工具"
                return self._text({"success": False, "error": error})
            arguments = json.loads(call.arguments or "{}")
            if not isinstance(arguments, dict):
                return self._text({"success": False, "error": "工具参数必须是对象"})
            if call.name in device_tool_names:
                if call.name.startswith("self.motion.") and call.name not in {"self.motion.stop", "self.motion.get_status"}:
                    duration = arguments.get("duration_ms")
                    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 5000:
                        return self._text({"success": False, "error": "运动工具必须使用 1 到 5000 毫秒的 duration_ms"})
                if request.device_mcp is None:
                    return self._text({"success": False, "error": "实机 MCP 未连接"})
                result = await request.device_mcp.call_tool(call.name, arguments)
            else:
                result = await self._rpc(client, request.session_id, "tools/call", {
                    "name": call.name, "arguments": arguments,
                })
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError):
            return self._text({"success": False, "error": "设备工具连接失败或响应超时"})
        except (ValueError, KeyError) as error:
            return self._text({"success": False, "error": str(error)})
        contents = result.get("content", [])
        structured = result.get("structuredContent") or {}
        # photo_data is deliberately excluded from text-chat context.
        summary = {key: value for key, value in structured.items() if key != "photo_data"}
        text_parts = [item["text"] for item in contents if item.get("type") == "text"]
        if text_parts:
            summary["tool_message"] = "\n".join(text_parts)
        if result.get("isError") or structured.get("success") is False:
            summary["success"] = False
            return self._text(summary)
        image = next((item for item in contents if item.get("type") == "image"), None)
        if image is None and structured.get("photo_data"):
            data = structured["photo_data"]
            image = {"data": data.split(",", 1)[-1], "mimeType": structured.get("mime_type", "image/jpeg")}
        if image:
            # Older browser clients echoed the question as their status message.
            # Only the visual model's description represents photo contents.
            for key in ("question", "message", "tool_message"):
                summary.pop(key, None)
            summary["photo_captured"] = True
            if self._vision is None:
                summary["vision_available"] = False
                summary["vision_description"] = "已拍照；没有配置视觉模型，不能解读照片内容。"
            else:
                try:
                    summary["vision_description"] = await self._vision.describe(
                        image["data"], image.get("mimeType", "image/jpeg"),
                        arguments.get("question") or request.text,
                    )
                    summary["vision_available"] = True
                except Exception as error:
                    summary["vision_available"] = False
                    summary["vision_description"] = f"已拍照，但视觉分析失败（{type(error).__name__}），不能判断照片内容。"
        return self._text(summary)

    @staticmethod
    def _text(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False)
