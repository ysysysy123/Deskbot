# 立创实战派 ESP32-S3 小智固件

本目录是 Deskbot 的立创实战派 ESP32-S3 固件工程。源码已替换为上游 `78/xiaozhi-esp32` 的最新同步版本，并选用立创板专用配置 `lckfb/szpi-esp32s3`；不再使用仓库原来的 ATK/双眼 UART 包装层。

## 构建目标

- board directory: `lckfb/szpi-esp32s3`
- board name: `lichuang-dev`
- target: `esp32s3`
- source revision: `64b57d0ba5c2f11a30974a0216dc28221c5b9d5b`（2026-09-23 同步）
- ESP-IDF: `6.0.1` 以上；本地 Docker 默认使用 `release-v6.1`

Ubuntu 编译不依赖宿主机 ESP-IDF，统一使用：

```text
/data/workspace/deskbot_work/docker/lichuang-s3-builder/
```

构建输出、日志和中间产物不放入公共仓库。

## 触摸排线容错

实机的 FT5x06 触摸排线已经断裂并拔除。`main/boards/lckfb/szpi-esp32s3/lichuang_dev_board.cc` 会在触摸 I²C 初始化失败时记录警告并继续启动，不创建触摸输入，不应再因触摸对象为空触发 `LoadProhibited`。

刷机前必须先完成纯编译和串口日志验证；当前阶段不擦除 Flash。

## 后续双轮舵机

用户确认 SG5010 为连续旋转舵机，因此后续控制接口必须使用“停止/正转/反转 + 速度”的语义，不能使用普通 0–180°位置舵机的角度语义。PCA9685 的 I²C 地址、供电和舵机电源隔离方案记录在本地工作目录的 `notes/hardware/`，待确认材料和引脚后再加入源码。
