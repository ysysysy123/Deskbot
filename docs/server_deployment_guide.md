# Deskbot 服务端与大模型远端部署全景指南 (Deployment Guide)

本文档面向需要将 **Qwen 27B 大模型** 或 **Deskbot 语音服务端** 部署到其他独立服务器（如局域网 GPU 工作站、机房算力服务器或公网云服务器）的场景。

---

## 架构拓扑选项

```text
【拓扑一：算力外置模式（最常见）】
[ESP32 小车] ──(WS)──> [本地/边缘 Linux: Deskbot 语音网关] ──(HTTP /v1)──> [外部 GPU 服务器: Qwen 27B (vLLM / Ollama)]
                       (负责 ASR, TTS, 记忆, 动作调度)                     (负责大模型推理)

【拓扑二：全栈迁移模式】
[ESP32 小车] ──(WS: 8000 / OTA: 8003)──────────────────────────────> [外部服务器 / 云主机]
                                                                        ├── Deskbot 语音服务 (Docker)
                                                                        └── Qwen 27B (本地显卡 / 容器)
```

---

## 第一部分：在另一台 GPU 服务器上部署 Qwen 27B

Qwen 2.5 27B 是目前性能与桌面陪伴体验极佳的模型：
- **完整精度 (BF16/FP16)**：约需 54GB~60GB 显存（建议双卡 RTX 3090/4090 或单卡 A100/A800 80G）。
- **4-bit 量化 (AWQ / GPTQ / GGUF Q4_K_M)**：仅需 **16GB ~ 20GB 显存**，**单张消费级 RTX 3090 / 4090 24G 即可全速流畅运行**（每秒 30~50+ tokens）。

### 方案 A：使用 vLLM 部署（推荐：吞吐最高、流式首字延迟极低）

1. **在 GPU 服务器上安装环境**：
   ```bash
   pip install vllm
   ```

2. **单卡 24G 显卡（RTX 3090 / 4090）运行 4-bit 量化版**：
   ```bash
   python3 -m vllm.entrypoints.openai.api_server \
     --model Qwen/Qwen2.5-27B-Instruct-GPTQ-Int4 \
     --host 0.0.0.0 \
     --port 8084 \
     --gpu-memory-utilization 0.92 \
     --max-model-len 8192 \
     --trust-remote-code
   ```

3. **双卡 24G（如 2x RTX 3090/4090）张量并行运行全量版**：
   ```bash
   python3 -m vllm.entrypoints.openai.api_server \
     --model Qwen/Qwen2.5-27B-Instruct \
     --tensor-parallel-size 2 \
     --host 0.0.0.0 \
     --port 8084 \
     --gpu-memory-utilization 0.90 \
     --max-model-len 8192
   ```

---

### 方案 B：使用 Ollama 部署（最简便：一条命令）

1. **安装 Ollama 并设置允许外部访问**：
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
2. **启动 Ollama 守护进程并监听所有网卡 (`0.0.0.0`)**：
   - 如果使用 Systemd：
     ```bash
     sudo systemctl edit ollama.service
     ```
     加入以下内容：
     ```ini
     [Service]
     Environment="OLLAMA_HOST=0.0.0.0:11434"
     Environment="OLLAMA_ORIGINS=*"
     ```
     保存后重载：
     ```bash
     sudo systemctl daemon-reload
     sudo systemctl restart ollama
     ```
   - 如果直接命令行调试：
     ```bash
     OLLAMA_HOST=0.0.0.0:11434 ollama serve
     ```
3. **拉取并运行 Qwen 27B**：
   ```bash
   ollama run qwen2.5:27b
   ```

---

### 方案 C：将 Deskbot 语音服务对齐到该外部服务器

在 Deskbot 语音服务所在的机器上，编辑 `server/voice/.env`：

```env
# 将 IP 替换为你的远端 GPU 服务器真实 IP（例如 192.168.10.200 或公网 IP）
VOICE_LLM_BASE_URL=http://192.168.10.200:8084/v1
VOICE_LLM_MODEL=qwen3.8-27b
VOICE_LLM_API_KEY=local
```
*(如果是 Ollama 则端口填 `11434`，模型填 `qwen2.5:27b`)*

重启 Deskbot 语音服务即可无缝接入远端 27B 算力！

---

## 第二部分：将整个 Deskbot 语音服务迁移部署到另一台服务器

如果你希望将整个 `server/voice`（负责 WebSocket 连接、ASR 语音转文字、TTS 语音合成与小车 MCP 控制）迁移到外部独立的 Linux 服务器：

### 1. 准备离线模型
在迁移前，需将 `server/voice/models` 复制到目标服务器：
- `models/snakers4_silero-vad` (VAD 语音活动检测)
- `models/SenseVoiceSmall` (ASR 语音识别)

### 2. 使用 Docker Compose 一键部署（推荐）

我们在 `server/voice/` 目录下已预置了标准 `Dockerfile` 与 `docker-compose.yml`：

1. **克隆代码与进入目录**：
   ```bash
   git clone -b feature/motor-control https://github.com/ysysysy123/Deskbot.git
   cd Deskbot/server/voice
   ```
2. **复制并修改配置文件**：
   ```bash
   cp .env.example .env
   cp config.example.yaml config.yaml
   ```
   在 `.env` 中按需配置大模型连接地址及本服务器对外 IP：
   ```env
   VOICE_SERVER_HOST=0.0.0.0
   VOICE_SERVER_WS_PORT=8000
   VOICE_SERVER_OTA_PORT=8003
   VOICE_SERVER_PUBLIC_WEBSOCKET_URL=ws://<目标服务器IP>:8000/xiaozhi/v1/
   ```
3. **一键启动**：
   ```bash
   docker compose up -d --build
   ```
4. **查看运行日志**：
   ```bash
   docker compose logs -f deskbot-voice
   ```

---

### 3. 使用裸机 Python 3.10 环境部署

如果不使用 Docker，在 Ubuntu/Debian 上执行：

```bash
# 1. 安装系统级音视频底层
sudo apt update && sudo apt install -y ffmpeg libopus0 libopus-dev

# 2. 创建并激活虚拟环境
python3.10 -m venv venv
source venv/bin/activate

# 3. 安装依赖包
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-music.txt

# 4. 启动服务
python run.py --config config.yaml
```

---

## 第三部分：ESP32 硬件小车对齐新服务器地址

当语音服务器的 IP 地址发生变更（例如从旧 IP 迁移到了新服务器 IP），小车需重新定位服务器：

### 方法一：进入小车 Web 配网页修改（最直观）
1. 短按小车配网按键或长按 BOOT 键触发重新配网模式。
2. 手机或电脑连接小车发出的 WiFi 热点（如 `Deskbot-Config`）。
3. 浏览器自动弹窗（或访问 `192.168.4.1`），在配置页面中修改：
   - **OTA 地址**：`http://<新服务器IP>:8003/xiaozhi/ota/`
4. 保存后小车自动重启并连接新服务器。

### 方法二：通过旧服务器平滑重定向（无感迁移）
如果旧服务器仍可运行片刻，只需在旧服务器的 `config.yaml` 或 `.env` 中设置：
```env
VOICE_SERVER_PUBLIC_WEBSOCKET_URL=ws://<新服务器IP>:8000/xiaozhi/v1/
```
小车发起一次 OTA 握手后，旧服务器会自动指示小车转连新服务器的 WebSocket。

### 方法三：保持统一局域网固定 IP 或内网域名
- 为新服务器在路由器 DHCP 中分配相同的 IP；
- 或者在局域网内搭建 mDNS / 本地 DNS 解析（例如 `deskbot.local`），固件端使用域名连接，未来更换机器无需重新烧录或重新配网。
