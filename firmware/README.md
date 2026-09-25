# 桌面宠物固件

本目录包含立创实战派 ESP32-S3 小智固件、DualEye 测试固件以及后续桌宠分层代码。公共的 ESP-IDF/小智源码来自上游 `78/xiaozhi-esp32`，本分支只对立创板配置和 Deskbot 集成入口做修改。

## 目录

| 路径 | 内容 |
| --- | --- |
| `lichuang-s3/source/xiaozhi-esp32/` | 立创实战派 ESP32-S3 专用配置及上游小智源码 |
| `dualeye-eye-test/` | DualEye Arduino 源码及本地 LVGL 库 |
| `tools/` | 当前仍适用的固件脚本；旧 ATK 脚本已移除 |
| `hal/`、`services/`、`orchestrator/` | 后续双轮、舵机和行为控制分层代码 |

## Ubuntu/Docker 构建

编译环境不安装到宿主机。构建说明、Dockerfile 和输出目录见：

- `lichuang-s3/source/xiaozhi-esp32/docker/firmware-builder/README.md`
- `/data/workspace/deskbot_work/docker/`

当前板型：

- board directory: `lckfb/szpi-esp32s3`
- board name: `lichuang-dev`
- target: `esp32s3`
- ESP-IDF: `6.0.1` 以上；本地容器优先使用 6.1

## 重要约定

- 触摸屏排线已断裂并拔除；固件必须允许 FT5x06 初始化失败后继续启动。
- 暂不刷机、不擦除 Flash；先完成纯编译，再由串口日志验证容错。
- 双轮舵机按用户确认的 SG5010 连续旋转舵机处理，不按 0–180°位置舵机处理。
- PCA9685 的供电和 I²C 线序在 `deskbot_work/notes/hardware/` 维护，不把未经验证的电源方案写进公共固件配置。
