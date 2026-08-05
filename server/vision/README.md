# Vision Service

职责：接收图像、调用多模态模型或本地视觉算法、输出结构化结果。

视觉模块不直接控制电机；它只发布事件，由调度层或语音服务决定是否生成白名单命令。第一项任务使用静态图片完成输入、结果和错误处理契约。

## 当前实现

当前分支先实现两个静态图片分析器：

- `local`：默认模式，不需要 API Key、模型权重或网络服务，使用 Python 标准库解析 PNG、JPEG、GIF、BMP 和 PPM P3 的基础信息。
- `zhipu`：云端视觉模式，通过 OpenAI-compatible chat completions 请求调用智谱 GLM 视觉模型。

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

使用 `.env` 中的 `VISION_PROVIDER` 自动选择适配器：

```powershell
python -m deskbot_vision.cli path\to\image.png --provider auto --env-file .env --pretty
```

运行测试：

```powershell
cd server\vision
python -m unittest discover tests
```

## 集成边界

视觉路线是：PC 客户端或设备摄像头采集图像，服务端通过 VLLM / Ollama / OpenAI-compatible 视觉模型返回图像解释。Deskbot 当前实现先固定其中的服务端输入输出契约：静态图片进入 `server/vision`，模块输出结构化结果，但不直接控制电机。

后续接模型时，只需要新增适配器并保持当前 JSON 契约稳定。
