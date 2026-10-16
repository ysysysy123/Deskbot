import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from aiohttp import web

from voice_server.camera_dialogue import CameraMcpDialogueBackend
from voice_server.dialogue import TurnRequest
from voice_server.providers.openai_compatible import OpenAICompatibleLLMProvider
from voice_server.providers.openai_vision import OpenAICompatibleVisionProvider


def chunk(text=None, *, index=0, name=None, arguments=None, call_id=None):
    calls = None
    if name is not None or arguments is not None:
        calls = [SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))]
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=calls))])


class FakeCompletions:
    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.calls = []
        self.closed = 0

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        items = next(self.rounds)

        async def stream():
            try:
                for item in items:
                    if isinstance(item, asyncio.Event):
                        await item.wait()
                    else:
                        yield item
            finally:
                self.closed += 1

        return stream()


def provider(rounds):
    completions = FakeCompletions(rounds)
    llm = OpenAICompatibleLLMProvider(
        base_url="http://unused", model="text-model", api_key="", temperature=0.1,
        max_tokens=100, timeout_s=1,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    return llm, completions


def request(text="请继续。"):
    return TurnRequest("device", "browser-session", "turn", text, [{"role": "user", "content": text}])


class FakeDeviceMcp:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return {"tools": [
            {"name": "self.motion.drive", "description": "驱动底盘", "inputSchema": {
                "type": "object", "properties": {
                    "linear": {"type": "integer"}, "angular": {"type": "integer"},
                    "duration_ms": {"type": "integer"},
                }, "required": ["duration_ms"],
            }},
            {"name": "self.sensor.get_imu", "description": "读取姿态", "inputSchema": {
                "type": "object", "properties": {},
            }},
        ]}

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"structuredContent": {"success": True, "message": "已执行"}, "content": [{"type": "text", "text": "已执行"}]}


async def mcp_server(aiohttp_client, results, *, tools=None):
    calls = []
    tools = tools if tools is not None else [
        {"name": name, "inputSchema": {"type": "object", "properties": {}}}
        for name in ("self_camera_start", "self_camera_take_photo", "self.camera.take_photo", "unrelated_tool")
    ]
    results = iter(results)

    async def rpc(incoming):
        body = await incoming.json()
        calls.append(body)
        result = {"tools": tools} if body["method"] == "tools/list" else next(results)
        return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": result})

    app = web.Application()
    app.router.add_post("/mcp", rpc)
    client = await aiohttp_client(app)
    return str(client.make_url("/mcp")), calls


async def test_camera_tool_rounds_aggregate_args_route_session_and_keep_images_out_of_text(aiohttp_client):
    url, rpc_calls = await mcp_server(aiohttp_client, [
        {"structuredContent": {"success": True, "open": True}},
        {"structuredContent": {"success": True, "photo_data": "data:image/jpeg;base64,PHOTO_BYTES",
                               "question": "桌上有什么？", "message": "桌上有什么？"},
         "content": [{"type": "image", "mimeType": "image/jpeg", "data": "PHOTO_BYTES"},
                     {"type": "text", "text": '{"message":"桌上有什么？"}'}]},
    ])
    llm, completions = provider([
        [chunk(name="self_camera_start", arguments="{}", call_id="open")],
        [chunk(name="self_camera_take_photo", arguments='{"question":"', call_id="photo"), chunk(arguments='桌上有什么？"}')],
        [chunk("桌上有一本书。")],
    ])
    vision_calls = []

    class VisionCompletions:
        async def create(self, **kwargs):
            vision_calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="桌上有一本书。"))])

    vision = OpenAICompatibleVisionProvider(
        base_url="http://unused", model="vision-model", api_key="", timeout_s=1,
        client=SimpleNamespace(chat=SimpleNamespace(completions=VisionCompletions())),
    )
    backend = CameraMcpDialogueBackend(llm, mcp_url=url, vision=vision)
    turn = request()
    turn.messages[:0] = [
        {"role": "system", "content": "请用简短的中文回答。"},
        {"role": "system", "content": "此前对话摘要：用户正在测试摄像头。"},
    ]
    original_messages = copy.deepcopy(turn.messages)
    assert [part async for part in backend.stream(turn)] == ["桌上有一本书。"]
    system_messages = [message for message in completions.calls[0]["messages"] if message["role"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["content"].startswith("请用简短的中文回答。\n\n此前对话摘要：用户正在测试摄像头。")
    assert "调用相应工具" in system_messages[0]["content"]
    assert turn.messages == original_messages
    assert all(call["params"]["_meta"] == {"deskbot/session_id": "browser-session"} for call in rpc_calls)
    assert rpc_calls[-1]["params"]["arguments"] == {"question": "桌上有什么？"}
    assert {tool["function"]["name"] for tool in completions.calls[0]["tools"]} == {"self_camera_start", "self_camera_take_photo"}
    tool_result = json.loads(completions.calls[-1]["messages"][-1]["content"])
    assert tool_result["vision_description"] == "桌上有一本书。"
    assert tool_result["photo_captured"] is True
    assert not {"question", "message", "tool_message"} & tool_result.keys()
    assert "PHOTO_BYTES" not in json.dumps(completions.calls)
    assert vision_calls[0]["model"] == "vision-model"
    assert vision_calls[0]["messages"][1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,PHOTO_BYTES"
    assert completions.closed == 3


async def test_device_mcp_tools_are_exposed_to_model_and_called_over_session(aiohttp_client):
    url, _ = await mcp_server(aiohttp_client, [], tools=[])
    device_mcp = FakeDeviceMcp()
    llm, completions = provider([
        [chunk(name="device_self_motion_drive", arguments='{"linear":30,"angular":0,"duration_ms":1000}', call_id="drive")],
        [chunk("已经向前走了一秒。")],
    ])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    turn = request("向前走一秒")
    turn = TurnRequest(turn.device_id, turn.session_id, turn.turn_id, turn.text, turn.messages, device_mcp)
    assert [part async for part in backend.stream(turn)] == ["设备已收到移动指令。"]
    assert device_mcp.calls == [("self.motion.drive", {"linear": 40, "angular": 0, "duration_ms": 1000})]
    assert not completions.calls


async def test_device_motion_requires_bounded_duration(aiohttp_client):
    url, _ = await mcp_server(aiohttp_client, [], tools=[])
    device_mcp = FakeDeviceMcp()
    llm, completions = provider([
        [chunk(name="self.motion.drive", arguments='{"linear":30,"angular":0,"duration_ms":6000}', call_id="drive")],
        [chunk("这次运动请求超过了安全时长。")],
    ])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    turn = request("向前走六秒")
    turn = TurnRequest(turn.device_id, turn.session_id, turn.turn_id, turn.text, turn.messages, device_mcp)
    assert [part async for part in backend.stream(turn)] == ["设备移动失败：运动工具必须使用 1 到 5000 毫秒的 duration_ms"]
    assert device_mcp.calls == []


@pytest.mark.parametrize("result, expected", [
    ({"isError": True, "content": [{"type": "text", "text": "用户拒绝摄像头权限"}]}, "用户拒绝摄像头权限"),
    ({"structuredContent": {"success": True}, "content": [{"type": "image", "data": "PHOTO_BYTES"}]}, "不能解读照片内容"),
])
async def test_tool_failure_and_missing_vision_are_reported_to_model(aiohttp_client, result, expected):
    url, _ = await mcp_server(aiohttp_client, [result])
    llm, completions = provider([
        [chunk(name="self_camera_take_photo", arguments="{}", call_id="photo")], [chunk("实际结果")],
    ])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    assert [part async for part in backend.stream(request())] == ["实际结果"]
    assert expected in completions.calls[-1]["messages"][-1]["content"]
    assert "PHOTO_BYTES" not in json.dumps(completions.calls)


async def test_tool_round_limit_allows_final_answer_without_further_tools(aiohttp_client):
    url, rpc_calls = await mcp_server(aiohttp_client, [{"structuredContent": {"success": True}}])
    llm, completions = provider([
        [chunk(name="self_camera_start", arguments="{}", call_id="open")], [chunk("摄像头已打开。")],
    ])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url, max_tool_rounds=1)
    assert [part async for part in backend.stream(request())] == ["摄像头已打开。"]
    assert len(rpc_calls) == 2
    assert "tools" not in completions.calls[-1]


async def test_unbound_browser_keeps_plain_chat_streaming(aiohttp_client):
    url, rpc_calls = await mcp_server(aiohttp_client, [], tools=[])
    llm, completions = provider([[chunk("你好"), chunk("。")]])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    assert [part async for part in backend.stream(request())] == ["你好", "。"]
    assert len(rpc_calls) == 1
    assert "tools" not in completions.calls[0]


async def test_abort_closes_tool_stream_and_does_not_execute_partial_call(aiohttp_client):
    url, rpc_calls = await mcp_server(aiohttp_client, [])
    gate = asyncio.Event()
    llm, completions = provider([[chunk("我来看看。"), chunk(name="self_camera_take_photo", arguments='{"', call_id="photo"), gate]])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    stream = backend.stream(request())
    assert await anext(stream) == "我来看看。"
    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert completions.closed == 1
    assert len(rpc_calls) == 1


async def test_abort_during_vision_propagates_cancellation(aiohttp_client):
    url, _ = await mcp_server(aiohttp_client, [{"content": [{"type": "image", "data": "PHOTO_BYTES"}]}])
    llm, completions = provider([])
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class Vision:
        async def describe(self, *args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    backend = CameraMcpDialogueBackend(llm, mcp_url=url, vision=Vision())
    task = asyncio.create_task(anext(backend.stream(request("打开摄像头看看看到了什么"))))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert not completions.calls


@pytest.mark.parametrize("text, tool, expected", [
    ("打开摄像头", "self_camera_start", "摄像头已打开。"),
    ("请关闭摄像头。", "self_camera_stop", "摄像头已关闭。"),
    ("把摄像头关掉", "self_camera_stop", "摄像头已关闭。"),
    ("可以打开摄像头吗？", "self_camera_start", "摄像头已打开。"),
])
async def test_explicit_camera_switch_executes_without_model(aiohttp_client, text, tool, expected):
    url, rpc_calls = await mcp_server(
        aiohttp_client, [{"structuredContent": {"success": True}}],
        tools=[{"name": tool}],
    )
    llm, completions = provider([])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    assert [part async for part in backend.stream(request(text))] == [expected]
    assert rpc_calls[-1]["params"]["name"] == tool
    assert rpc_calls[-1]["params"]["_meta"] == {"deskbot/session_id": "browser-session"}
    assert not completions.calls


@pytest.mark.parametrize("text", ["不要打开摄像头。", "如何使用摄像头？", "不要拍照", "你好"])
async def test_camera_discussion_and_plain_chat_keep_model_streaming(aiohttp_client, text):
    url, rpc_calls = await mcp_server(aiohttp_client, [], tools=[{"name": "self_camera_stop"}])
    llm, completions = provider([[chunk("由模型"), chunk("理解请求。")]])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    assert [part async for part in backend.stream(request(text))] == ["由模型", "理解请求。"]
    assert len(rpc_calls) == 1
    assert len(completions.calls) == 1


@pytest.mark.parametrize("tools, results, expected", [
    ([], [], "当前会话没有此摄像头工具"),
    ([{"name": "self_camera_start"}], [{"structuredContent": {"success": False, "error": "用户拒绝摄像头权限"}}], "用户拒绝摄像头权限"),
])
async def test_explicit_camera_switch_reports_actual_failure(aiohttp_client, tools, results, expected):
    url, _ = await mcp_server(aiohttp_client, results, tools=tools)
    llm, completions = provider([])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    answer = "".join([part async for part in backend.stream(request("打开摄像头"))])
    assert "打开失败" in answer and expected in answer
    assert "已打开" not in answer
    assert not completions.calls


@pytest.mark.parametrize("text", [
    "打开摄像头看看看到了什么",
    "打开摄像头然后拍照",
    "拍照",
    "拍摄像头。",
    "你看到了什么？",
    "看一下当前摄像头画面",
    "看看我手里是什么",
    "看看桌上有什么",
    "看看镜头里这个怎么用",
])
async def test_explicit_observation_takes_one_photo_and_returns_actual_vision(aiohttp_client, text):
    # A closed camera needs only take_photo: the browser opens it as necessary.
    url, rpc_calls = await mcp_server(
        aiohttp_client,
        [{"structuredContent": {"success": True},
          "content": [{"type": "image", "mimeType": "image/jpeg", "data": "PHOTO_BYTES"}]}],
        tools=[{"name": "self_camera_take_photo"}],
    )
    llm, completions = provider([])
    vision_calls = []

    class Vision:
        async def describe(self, *args):
            vision_calls.append(args)
            return "画面中有一个蓝色杯子。"

    backend = CameraMcpDialogueBackend(llm, mcp_url=url, vision=Vision())
    assert [part async for part in backend.stream(request(text))] == ["画面中有一个蓝色杯子。"]
    assert [call["method"] for call in rpc_calls] == ["tools/list", "tools/call"]
    assert rpc_calls[-1]["params"] == {
        "name": "self_camera_take_photo", "arguments": {"question": text},
        "_meta": {"deskbot/session_id": "browser-session"},
    }
    assert vision_calls == [("PHOTO_BYTES", "image/jpeg", text)]
    assert not completions.calls


@pytest.mark.parametrize("result, expected", [
    ({"isError": True, "content": [{"type": "text", "text": "用户拒绝摄像头权限"}]}, "用户拒绝摄像头权限"),
    ({"structuredContent": {"success": True}, "content": [{"type": "image", "data": "PHOTO_BYTES"}]}, "没有配置视觉模型"),
    ({"structuredContent": {"success": True}}, "工具没有返回可分析的照片"),
])
async def test_explicit_observation_reports_actual_error_without_model(aiohttp_client, result, expected):
    url, _ = await mcp_server(aiohttp_client, [result])
    llm, completions = provider([])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    answer = "".join([part async for part in backend.stream(request("打开摄像头看看看到了什么"))])
    assert expected in answer
    assert "PHOTO_BYTES" not in answer
    assert not completions.calls


async def test_explicit_observation_reports_missing_camera_tool(aiohttp_client):
    url, rpc_calls = await mcp_server(aiohttp_client, [], tools=[])
    llm, completions = provider([])
    backend = CameraMcpDialogueBackend(llm, mcp_url=url)
    answer = "".join([part async for part in backend.stream(request("看看我手里是什么"))])
    assert "当前会话没有此摄像头工具" in answer
    assert len(rpc_calls) == 1
    assert not completions.calls


async def test_explicit_observation_reports_vision_failure_without_model(aiohttp_client):
    url, _ = await mcp_server(aiohttp_client, [{"content": [{"type": "image", "data": "PHOTO_BYTES"}]}])
    llm, completions = provider([])

    class Vision:
        async def describe(self, *args):
            raise RuntimeError("vision unavailable")

    backend = CameraMcpDialogueBackend(llm, mcp_url=url, vision=Vision())
    answer = "".join([part async for part in backend.stream(request("看看桌上有什么"))])
    assert "视觉分析失败" in answer
    assert not completions.calls

class FirmwareMcp:
    def __init__(self, tools, result):
        self.tools = tools
        self.result = result
        self.calls = []

    async def list_tools(self):
        return {"tools": [{"name": name} for name in self.tools]}

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def device_turn(text, device):
    turn = request(text)
    return TurnRequest(turn.device_id, turn.session_id, turn.turn_id, text, turn.messages, device)


async def test_firmware_boolean_stop_reports_success_without_browser_or_model():
    llm, completions = provider([])
    device = FirmwareMcp(["self.motion.stop"], {"isError": False, "content": [{"type": "text", "text": "true"}]})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    assert [part async for part in backend.stream(device_turn("马上停下", device))] == ["设备已停止。"]
    assert device.calls == [("self.motion.stop", {})]
    assert not completions.calls


@pytest.mark.parametrize("text,duration", [
    ("往前走一步", 500),
    ("往前再走一步。", 500),
    ("往前走两步。", 1000),
    ("你往前移动一下。", 500),
    ("你好，小志往前走。", 500),
    ("可以往前走一下吗？", 500),
])
async def test_firmware_short_forward_command_calls_drive_without_model_confirmation(text, duration):
    llm, completions = provider([])
    device = FirmwareMcp(["self.motion.drive"], {"isError": False, "content": [{"type": "text", "text": "true"}]})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    assert [part async for part in backend.stream(device_turn(text, device))] == ["设备已收到移动指令。"]
    assert device.calls == [("self.motion.drive", {"linear": 40, "angular": 0, "duration_ms": duration})]
    assert not completions.calls


async def test_repeat_movement_uses_previous_user_direction():
    llm, completions = provider([])
    device = FirmwareMcp(["self.motion.drive"], {"content": [{"type": "text", "text": "true"}]})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    turn = device_turn("再走一步。", device)
    turn.messages.insert(-1, {"role": "user", "content": "往后走一步。"})
    assert [part async for part in backend.stream(turn)] == ["设备已收到移动指令。"]
    assert device.calls == [("self.motion.drive", {"linear": -40, "angular": 0, "duration_ms": 500})]
    assert not completions.calls


@pytest.mark.parametrize("text", ["不要往前走。", "怎么让你往前走？"])
async def test_movement_discussion_does_not_directly_execute(text):
    llm, completions = provider([[chunk("好的。")]])
    device = FirmwareMcp(["self.motion.drive"], {})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    assert [part async for part in backend.stream(device_turn(text, device))] == ["好的。"]
    assert not device.calls


async def test_large_step_request_reports_limit_without_model_success_claim():
    llm, completions = provider([])
    device = FirmwareMcp(["self.motion.drive"], {})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    answer = "".join([part async for part in backend.stream(device_turn("往前走100步。", device))])
    assert "设备移动失败" in answer and "5000" in answer
    assert not device.calls and not completions.calls


async def test_firmware_drive_failure_is_reported_without_claiming_success():
    llm, completions = provider([])
    device = FirmwareMcp(["self.motion.drive"], {"isError": True, "content": [{"type": "text", "text": "ESP_ERR_INVALID_STATE"}]})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    answer = "".join([part async for part in backend.stream(device_turn("往前走一步", device))])
    assert "设备移动失败" in answer and "ESP_ERR_INVALID_STATE" in answer
    assert not completions.calls


@pytest.mark.parametrize("text", ["打开摄像头", "看看前面有什么", "拍张照"])
async def test_firmware_camera_uses_uploaded_description_without_browser(text):
    llm, completions = provider([])
    result = {"success": True, "photo_captured": True, "vision_description": "桌上有一个杯子。"}
    device = FirmwareMcp(["self.camera.take_photo"], {"isError": False, "content": [{"type": "text", "text": json.dumps(result)}]})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    assert [part async for part in backend.stream(device_turn(text, device))] == ["桌上有一个杯子。"]
    assert device.calls == [("self.camera.take_photo", {"question": text})]
    assert not completions.calls


async def test_firmware_camera_errors_do_not_look_like_description():
    llm, _ = provider([])
    device = FirmwareMcp(["self.camera.take_photo"], {"isError": True, "content": [{"type": "text", "text": "Failed to capture photo"}]})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    reply = "".join([part async for part in backend.stream(device_turn("拍照", device))])
    assert "无法查看" in reply and "Failed to capture photo" in reply


async def test_firmware_status_and_volume_are_available():
    llm, completions = provider([[chunk("当前工具已就绪。")]])
    device = FirmwareMcp(["self.get_device_status", "self.audio_speaker.set_volume"], {})
    backend = CameraMcpDialogueBackend(llm, mcp_url="http://unreachable.invalid")
    await anext(backend.stream(device_turn("你能做什么", device)))
    names = {tool["function"]["name"] for tool in completions.calls[0]["tools"]}
    assert names == {"device_self_get_device_status", "device_self_audio_speaker_set_volume"}
