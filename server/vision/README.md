# Vision Service

职责：接收图像、调用多模态模型或本地视觉算法、输出结构化结果。

视觉模块不直接控制电机；它只发布事件，由调度层或语音服务决定是否生成白名单命令。第一项任务使用静态图片完成输入、结果和错误处理契约。

## 当前实现

当前实现包含三类入口：

- `local`：默认模式，不需要 API Key、模型权重或网络服务，使用 Python 标准库解析 PNG、JPEG、GIF、BMP 和 PPM P3 的基础信息。
- `zhipu`：云端视觉模式，通过 OpenAI-compatible chat completions 请求调用智谱 GLM 视觉模型。
- PC 摄像头拍照：通过可选依赖 OpenCV 从 PC 摄像头采集一帧，再进入同一套视觉分析链路。
- HTTP 视觉服务：默认使用 `8005` 端口，提供 `/mcp/vision/explain`。`8003` 已由新的语音服务用于 OTA 和健康检查，因此视觉服务不能再占用它。

两种模式输出相同的稳定 JSON 契约，后续可继续增加 Ollama 或其他 OpenAI-compatible 视觉模型适配器。

输出结果包含：

- `schema_version`：视觉结果契约版本；
- `adapter`：当前适配器名称；
- `image`：格式、尺寸、字节数、方向；
- `summary`：面向上层服务的简短描述；
- `tags`：结构化标签；
- `findings`：带置信度的发现项；
- `errors`：错误列表。

## 运行方式

准备 Python 3.9 或更高版本。不需要额外安装依赖。

```powershell
python --version
```

运行样例图片分析：

```powershell
cd server\vision
python -m deskbot_vision.cli samples\deskbot-scene.ppm --pretty
```

写入 JSON 文件：

```powershell
python -m deskbot_vision.cli samples\deskbot-scene.ppm --pretty --output .\build\vision-result.json
```

## PC 摄像头拍照

摄像头采集依赖 OpenCV。只在需要使用 `--capture-camera` 时安装：

```powershell
python -m pip install opencv-python
```

扫描可用摄像头索引：

```powershell
cd server\vision
python -m deskbot_vision.cli --list-cameras --pretty
```

拍照并使用本地静态分析：

```powershell
cd server\vision
python -m deskbot_vision.cli --capture-camera .\build\camera.jpg --pretty
```

拍照并调用云端视觉：

```powershell
python -m deskbot_vision.cli --capture-camera .\build\camera.jpg --provider auto --env-file .env --prompt "请描述摄像头看到的画面，并输出给桌面陪伴机器人调度层使用。" --pretty --retries 2 --retry-delay 3 --output .\build\camera-cloud-result.json
```

如果有多个摄像头，可以通过 `--camera-index` 指定：

```powershell
python -m deskbot_vision.cli --capture-camera .\build\camera.jpg --camera-index 1 --pretty
```

## 智谱云端视觉

复制 `.env.example` 为 `.env`，并填写 API Key：

```powershell
copy .env.example .env
```

配置项：

```text
VISION_PROVIDER=zhipu
ZHIPUAI_API_KEY=
ZHIPUAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPUAI_VISION_MODEL=glm-4.6v-flash
ZHIPUAI_THINKING=disabled
```

运行云端视觉分析：

```powershell
python -m deskbot_vision.cli path\to\image.png --provider zhipu --env-file .env --prompt "Describe the image." --pretty
```

如果云端模型返回 HTTP 429，表示模型繁忙或被限流，可以加重试参数：

```powershell
python -m deskbot_vision.cli path\to\image.png --provider zhipu --env-file .env --prompt "Describe the image." --pretty --retries 3 --retry-delay 10
```

使用 `.env` 中的 `VISION_PROVIDER` 自动选择适配器：

```powershell
python -m deskbot_vision.cli path\to\image.png --provider auto --env-file .env --pretty
```

## HTTP 视觉服务

启动本地视觉 HTTP 服务，默认使用 `8005`，避免与语音服务的 OTA/健康检查端口 `8003` 冲突：

```powershell
cd server\vision
python -m deskbot_vision.http_server --host 127.0.0.1 --port 8005 --provider auto --env-file .env --retries 2 --retry-delay 3
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8005/health
Invoke-RestMethod http://127.0.0.1:8005/mcp/vision/explain
```

用本地图片调用 `/mcp/vision/explain`：

```powershell
$body = @{
  request_id = "pc-test-001"
  device_id = "pc-camera"
  session_id = "local-demo"
  image_path = ".\build\a.jpg"
  provider = "auto"
  prompt = "请详细描述这张图片里有什么，重点说明物体、场景和可能的用途。"
} | ConvertTo-Json -Depth 6

Invoke-RestMethod http://127.0.0.1:8005/mcp/vision/explain -Method Post -ContentType "application/json" -Body $body
```

请求 JSON 支持两种图片输入：

- `image_path`：服务端本机可访问的图片路径，适合 PC 端和本地调试；
- `image_base64` + `mime_type`：客户端把摄像头图像编码后上传，适合后续设备或协议层接入。

也支持 ATK 小智固件当前使用的 `multipart/form-data` 上传：字段为 `question` 和 `file`，并读取 `Device-Id`、`Client-Id` 请求头。固件 MCP capability 的 `vision.url` 应配置为 `http://<PC 局域网 IP>:8005/mcp/vision/explain`，不能指向语音服务的 `8003`。

成功响应会返回：

- `result`：视觉分析结果，结构与 CLI 输出一致；
- `event.type = vision.analysis.completed`；
- `event.target = dispatcher`。
- `protocol_event`：与 `protocol/` 对齐的 `version: "0.1"`、`type: "vision.event"` 控制消息，可由调度层转发或消费。

失败响应会返回：

- `result = null`；
- `event.type = vision.analysis.failed`；
- `errors[]`：错误码和错误说明。

运行测试：

```powershell
cd server\vision
python -m unittest discover tests
```

## 集成边界

视觉路线是：PC 客户端或设备摄像头采集图像，服务端通过 VLLM / Ollama / OpenAI-compatible 视觉模型返回图像解释。Deskbot 当前实现先固定其中的服务端输入输出契约：静态图片进入 `server/vision`，模块输出结构化结果，但不直接控制电机。

视觉模块不直接控制电机；它只发布视觉事件，由调度层或语音服务决定是否生成白名单动作命令。

后续接模型时，只需要新增适配器并保持当前 JSON 契约稳定。
