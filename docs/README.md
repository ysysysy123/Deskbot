# Deskbot 开发与架构文档索引 (Documentation Index)

欢迎阅读 Deskbot 项目开发文档。本文档库按层级整理了硬件规格、抽象 API、系统架构及历史调试踩坑记忆，方便二次开发与系统接手。

---

## 核心文档导航

### 1. 外设接口与二次开发 (Secondary Development)
- **[外设接口与二次开发指南 (API Reference)](peripherals_api.md)**
  - 双轮运动控制抽象接口 (`MotionController`, `Forward`, `Backward`, `TurnLeft`, `TurnRight`, `SetWheelSpeeds`)
  - 6轴陀螺仪姿态感知接口 (`Qmi8658`, `ImuData`, $a_x/a_y/a_z$, $g_x/g_y/g_z$, Pitch/Roll)
  - 视觉摄像头图像读取与大模型问答接口 (`Esp32Camera`, `Capture`, `GetCurrentFrameBuffer`, `Explain`)
  - 大模型 MCP 工具调用协议 (`self.motion.*`, `self.sensor.*`, `self.camera.*`)
  - 极简二次开发 C++ 示例代码
- **[MCP 服务端开发与外设控制接手指南 (MCP Developer Handover Guide)](mcp_server_developer_guide.md)**
  - 专为负责服务端 MCP 接口、大模型 Function Calling 与业务编排的开发者编写
  - 舵机运动控制 MCP 工具 (`self.motion.drive`, `self.motion.turn`, `self.motion.stop`, `self.motion.get_status`)
  - 摄像头画面获取与视觉分析 (`self.camera.take_photo`)
  - 麦克风控制与音频流管理（WebSocket 协议指令控制 `listen: start/stop` 与 MCP 静音扩展）
  - 6轴陀螺仪姿态遥测 (`self.sensor.get_imu`)
  - 本地 Python 自动化联调脚本与调试速查

### 2. 调试记忆与接手指南 (Handover & Lessons Learned)
- **[调试记忆与接手文档 (Debugging Memory & Handover Guide)](debugging_and_handover.md)**
  - 供电排查复盘：电池供电灰屏闪烁/无限重启的物理原因（浪涌电流、升压压降、Brownout Detector）与软硬件应对方案
  - 稳定性复盘：Core 1 Panic 崩溃根因排查与 LVGL 多线程安全守则 (`DisplayLockGuard`)
  - 视觉调试复盘：GC2145 DVP 摄像头花屏/行条纹成因与 PCLK 时钟极性、DMA SRAM 优化
  - 姿态遥测排查：QMI8658 字体与 UI 排版紧凑优化
  - 硬件总线拓扑速查表（I2C1 从机地址分布、SPI 屏幕引脚、DVP 摄像头引脚）
  - 高阶智能体开发指南：对话式语音移动控制、多模态自主探索 Agent、IMU 姿态防跌落保护
  - 编译构建与固件烧录速查（Docker 增量编译、esptool 烧录命令）

### 3. 系统架构与决策记录 (Architecture & ADR)
- **[系统架构总览 (System Architecture)](architecture.md)**：分层设计理念与软硬件通信原则。
- **[架构决策记录 (ADR)](decisions/0001-modular-monorepo.md)**：模块化 Monorepo 演进决策。
- **[路线图 (Roadmap)](roadmap.md)**：项目演进规划与功能里程碑。
- **[团队工作流规范 (Team Workflow)](team-workflow.md)**：分支管理与提交流程。
