# Deskbot MCP 服务端开发与外设控制接手指南 (MCP Developer Handover Guide)

本文档专为负责**开发服务端 MCP (Model Context Protocol) 服务、大模型 Function Calling 以及业务编排**的同伴编写。
文档涵盖了**当前分支状态与代码全景**、**舵机运动控制**、**摄像头画面获取与视觉分析**、**麦克风控制与音频流管理**、**6轴陀螺仪遥测**以及**本地联调脚本**。

---

## 一、当前分支位置与代码全景 (Current Branch & Codebase Manifest)

### 1. Git 分支状态
- **主线分支**：`main`（当前 HEAD 指向最新提交 `506d20c`，已与远程 `origin/main` 严格对齐）。
- **同步特性分支**：`feature/lichuang-s3-latest`（最新提交 `506d20c`，已与远程 `origin/feature/lichuang-s3-latest` 严格对齐）。
- **最近修复**：解决了 2.5.0 新固件在 WebSocket 首包中带 `text_font` 导致服务端 1002 断开的协议兼容问题；当前硬件已在实机上通过 WebSocket 成功握手、分配 Session ID，并完成双向语音对话。

### 2. 仓库核心模块目录结构
```text
Deskbot/
├── docs/                                  # 开发者与架构文档库
│   ├── README.md                          # 文档索引导航
│   ├── mcp_server_developer_guide.md      # 本文档：MCP 服务端与外设控制接手指南
│   ├── peripherals_api.md                 # 板级外设 C++ 抽象接口文档
│   └── debugging_and_handover.md          # 硬件调试踩坑记忆与系统底线
├── firmware/
│   └── lichuang-s3/source/xiaozhi-esp32/  # ESP32-S3 实战派完整固件源码
│       ├── main/
│       │   ├── boards/lckfb/szpi-esp32s3/ # 立创实战派板级支持（舵机、摄像头、IMU、UI）
│       │   ├── mcp_server.cc/.h           # 设备端 MCP 工具注册与分发引擎
│       │   └── protocols/                 # WebSocket 客户端协议实现
│       └── build/                         # 固件构建产物 (xiaozhi.bin, merged-binary.bin)
└── server/
    ├── voice/                             # 语音服务端（Python 异步 WebSocket 服务）
    │   ├── src/voice_server/
    │   │   ├── websocket_server.py        # WebSocket 会话管理器与连接处理器
    │   │   ├── camera_mcp.py              # 摄像头 MCP 本地桥接
    │   │   ├── dialogue.py                # 语音会话与大模型 Tool Calling 调度
    │   │   └── ota.py                     # 设备激活与 OTA 服务
    └── vision/                            # 视觉服务端（多模态大模型图像分析服务）
```

---

## 二、舵机与双轮运动控制 (Servo / Motion Control via MCP)

### 1. 硬件底盘动力学特性
- **控制硬件**：PCA9685 16 通道 12-bit PWM 控制器（I2C 地址 `0x40`，频率 50Hz）。
- **执行机构**：双路 SG5010 360° 连续旋转舵机（左轮 Channel 0，右轮 Channel 1）。
- **实测死区与中位标定**：
  - 硬件实测死区范围：`1510 µs ~ 1610 µs`
  - 静止零位脉冲：**`1560 µs`**（固件已做偏置，输入速度 0 时完全静止，无微漂）
- **速度百分比抽象**：`-100 ~ 100`（正数前进，负数后退，0 刹车）。
- **安全看门狗超时机制**：所有运动指令均强制带 `duration_ms` 超时自动停机（默认 500ms，最大 5000ms），防止因网络断连导致小车失控撞墙。

### 2. 设备端已注册的运动控制 MCP 工具清单

设备端固件在连接成功后，会自动向 MCP 服务端暴露以下工具声明（JSON Schema）：

#### ① `self.motion.drive` (综合底盘差速驱动)
- **描述**：控制小车按指定线速度和角速度行驶一段毫秒数后自动停止。
- **参数说明**：
  - `linear` (integer, 默认 30, 范围 `-100..100`): 前进/后退速度百分比（正数前进，负数后退）。
  - `angular` (integer, 默认 0, 范围 `-100..100`): 转向分量（正数向右转向，负数向左转向）。
  - `duration_ms` (integer, 默认 500, 范围 `1..5000`): 运动持续毫秒数。
- **示例入参**：
  ```json
  {"name": "self.motion.drive", "arguments": {"linear": 35, "angular": 0, "duration_ms": 1200}}
  ```
- **返回值**：`true`（成功）或错误文本。

#### ② `self.motion.turn` (原地自转调头)
- **描述**：原地旋转指定角速度和时长（双轮对转）。
- **参数说明**：
  - `angular` (integer, 默认 25, 范围 `-100..100`): 原地旋转速度（正数向右顺时针，负数向左逆时针）。
  - `duration_ms` (integer, 默认 500, 范围 `1..5000`): 旋转持续毫秒数。
- **示例入参**：
  ```json
  {"name": "self.motion.turn", "arguments": {"angular": 30, "duration_ms": 800}}
  ```

#### ③ `self.motion.stop` (紧急制动)
- **描述**：立即刹车，将左右轮 PWM 强制复位至静止中位 1560µs。
- **参数说明**：无。
- **示例入参**：
  ```json
  {"name": "self.motion.stop", "arguments": {}}
  ```

#### ④ `self.motion.get_status` (底盘遥测查询)
- **描述**：查询当前轮速百分比和硬件底层微秒脉冲值。
- **返回 JSON**：
  ```json
  {"left_speed": 0, "right_speed": 0, "left_pulse_us": 1560, "right_pulse_us": 1560, "neutral_us": 1560}
  ```

---

## 三、摄像头视觉获取与大模型分析 (Camera via MCP)

### 1. 硬件规格与时序
- **感光芯片**：GC2145 DVP 8-bit CMOS，320×240（QVGA）。
- **画面特性**：已完成 PCLK 极性反转与内部 SRAM DMA 缓冲优化，画面无噪点条纹，色彩干净。

### 2. 视觉调用链路与 MCP 工具

#### ① 设备端已内置工具：`self.camera.take_photo`
- **工具描述**：指示设备拍摄一张照片并发送至视觉模型进行分析。
- **参数说明**：
  - `question` (string): 提示词或要分析的问题（例如："画面正前方是否有障碍物？"）。
- **固件底层处理流程**：
  1. 设备端捕获最新一帧原始 RGB565 画面；
  2. 软编码/转码为标准 JPEG 二进制字节流；
  3. 通过 HTTP POST 发送到配置的视觉解释地址（Explain URL）；
  4. 将多模态大模型的描述文字作为 MCP Tool 返回结果返回。

#### ② 服务端多模态大模型对接建议（Python）
服务端在处理视觉时，通常需要将捕获的图像交给多模态视觉模型（如 GPT-4o, GLM-4V, Qwen-VL）：
```python
# 示例：构建多模态大模型视觉提示词
def build_vision_message(image_jpeg_bytes: bytes, user_prompt: str) -> dict:
    base64_img = base64.b64encode(image_jpeg_bytes).decode('utf-8')
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": user_prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}
            }
        ]
    }
```

---

## 四、麦克风控制与音频流管理 (Microphone & Audio Stream)

### 1. 硬件音频流水线
- **采集芯片**：ES7210 4-Mic ADC，I2S TDM 模式采集。
- **本地降噪与唤醒**：板载跑 Espressif AEC 声学回声消除 + WebRTC VAD + WakeNet（唤醒词“你好小智”）。
- **流式格式**：Opus 编码，单声道 16000Hz 采样率，每包 60ms。

### 2. 服务端如何控制麦克风与倾听状态

在 Deskbot 的 WebSocket 语音协议中，**服务端的 WebSocket 连接对设备的麦克风状态拥有最高控制权**。服务端无需硬编码 GPIO，只需通过下发标准的 JSON 协议报文即可控制麦克风：

| 控制意图 | 服务端向设备下发的报文 | 说明与设备反应 |
| :--- | :--- | :--- |
| **开启麦克风 (开始倾听)** | `{"type": "listen", "state": "start", "mode": "auto"}` | 设备立刻开启麦克风拾音并向服务端持续推流 Opus 音频 |
| **静音麦克风 (停止倾听)** | `{"type": "listen", "state": "stop"}` | 设备立即停止向服务端发送麦克风音频流 |
| **打断当前说话与推流** | `{"type": "abort", "reason": "user_interrupted"}` | 立即打断当前扬声器播报与音频推流 |

### 3. 如果需要为大模型增加 `self.audio_mic.mute` MCP 工具
若希望让大模型通过 MCP 显式关闭/静音麦克风，可在服务端的会话处理器（`VoiceSession`）中添加 MCP 工具：
```python
# 在服务端会话层注册控制麦克风的工具
async def handle_mute_microphone(session, mute: bool):
    if mute:
        # 服务端通知设备停止倾听推流
        await session.transport.send_json({"type": "listen", "state": "stop"})
    else:
        # 服务端通知设备恢复倾听
        await session.transport.send_json({"type": "listen", "state": "start", "mode": "auto"})
```

---

## 五、6轴陀螺仪姿态感知 (IMU via MCP)

### 1. 设备端工具：`self.sensor.get_imu`
- **描述**：获取当前板卡的实时 6 轴加速度、角速度以及融合解算后的 Pitch（俯仰角）和 Roll（横滚角）。
- **参数说明**：无。
- **返回数据样例**：
  ```json
  {
    "ax": 0.02,
    "ay": -0.01,
    "az": 0.99,
    "gx": 0.1,
    "gy": 0.0,
    "gz": -0.2,
    "pitch": -0.5,
    "roll": 1.2
  }
  ```
- **典型应用**：
  - **跌倒防护**：当 $|pitch| > 35^\circ$ 或 $|roll| > 35^\circ$ 或 $az < 0.3g$ 时判定小车已翻倒或处于悬空掉落边缘，立即触发 `self.motion.stop`。
  - **撞障检测**：当水平合力 $\sqrt{ax^2 + ay^2} > 1.8g$ 时判定撞墙，触发倒退脱困动作。

---

## 六、本地联调与快速测试脚本 (Developer Quickstart)

为方便服务端开发者在不需要对硬件说话的情况下快速验证 MCP 工具调用，可使用以下 Python 脚本直连设备所在的 WebSocket 端口进行自动化调试：

```python
#!/usr/bin/env python3
"""Deskbot MCP 联调测试工具脚本"""
import asyncio
import json
from websockets.asyncio.client import connect

DEVICE_WS_URL = "ws://192.168.10.111:8000/xiaozhi/v1/"
DEVICE_MAC = "10:51:db:75:de:50"

async def test_deskbot_mcp():
    print(f"Connecting to {DEVICE_WS_URL} ...")
    async with connect(DEVICE_WS_URL, additional_headers={"Device-Id": DEVICE_MAC}) as ws:
        # 1. 发送标准客户端 hello 包
        hello_msg = {
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "features": {"mcp": True},
            "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}
        }
        await ws.send(json.dumps(hello_msg))
        server_hello = await ws.recv()
        print("Received Server Hello:", server_hello)

        # 2. 模拟下发 MCP 工具调用：让小车向前行驶 600 毫秒
        tool_call_drive = {
            "type": "mcp",
            "method": "tools/call",
            "params": {
                "name": "self.motion.drive",
                "arguments": {"linear": 30, "angular": 0, "duration_ms": 600}
            }
        }
        print("Sending motion drive tool call...")
        await ws.send(json.dumps(tool_call_drive))

        # 3. 模拟下发查询陀螺仪姿态工具调用
        tool_call_imu = {
            "type": "mcp",
            "method": "tools/call",
            "params": {"name": "self.sensor.get_imu", "arguments": {}}
        }
        print("Sending get_imu tool call...")
        await ws.send(json.dumps(tool_call_imu))

        # 监听回包
        while True:
            msg = await ws.recv()
            if isinstance(msg, str):
                print("Received text packet:", msg)
            else:
                print(f"Received binary packet ({len(msg)} bytes)")

if __name__ == "__main__":
    asyncio.run(test_deskbot_mcp())
```

---

## 七、日常编译烧录与串口监控速查

| 操作 | 对应命令 | 说明 |
| :--- | :--- | :--- |
| **Docker 容器增量编译** | `bash /data/workspace/deskbot_work/docker/lichuang-s3-builder/build.sh build` | 极速增量构建，产物生成至 build 目录 |
| **固件高速烧录** | `python3 -m esptool --chip esp32s3 -p /dev/ttyUSB0 -b 921600 write-flash 0x20000 Deskbot/firmware/lichuang-s3/source/xiaozhi-esp32/build/xiaozhi.bin` | 烧录 app 分区 |
| **串口日志实时监控** | `python3 -m serial.tools.miniterm /dev/ttyACM0 115200 --raw` | USB-JTAG 实时控制台 |
| **模拟按下 BOOT 键** | `python3 -c "import serial, time; s=serial.Serial('/dev/ttyUSB0',115200); s.setDTR(True); time.sleep(0.2); s.setDTR(False)"` | 触发对话状态切换 |
