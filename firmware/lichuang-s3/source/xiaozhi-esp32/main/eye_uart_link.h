#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "device_state.h"

/**
 * 立创实战派 ESP32-S3 → 微雪 DualEye 1.28 的串口联动模块。
 *
 * 接线（初次联调只接三根，两板各自 USB 供电）：
 *   立创 J2-3  GND      → 双眼 P2-2  GND
 *   立创 J2-2  GPIO10   → 双眼 P2-9  UART_RXD    (本模块 TX)
 *   立创 J2-1  GPIO11   ← 双眼 P2-10 UART_TXD    (本模块 RX)
 *   （立创 J2-5 VBUS → 双眼 P2-1 USB_5V，确认稳定后再接）
 *
 * 协议（换行分隔，不区分大小写，每行 ≤ 63 字节）：
 *   PING            → PONG 1
 *   STATE <NAME>    → OK STATE <NAME>
 *   GAZE <x> <y>    → OK GAZE          x/y ∈ [-100,100]，负 x 左、负 y 上
 *   BLINK           → OK BLINK
 * 双眼上电主动发送：READY EYE_UART_V1
 */
class EyeUartLink final {
public:
    /** 初始化 UART1 并启动接收任务（可重复调用，幂等）。 */
    static esp_err_t Init();

    /** 是否已与双眼板建立链路（收到过 READY 或 PONG）。 */
    static bool IsLinked();

    /** 打开/关闭调试日志。 */
    static void SetVerbose(bool enable);

    // ---- 命令 ----
    static esp_err_t SendState(const char* state);
    static esp_err_t SendGaze(int x_percent, int y_percent);
    static esp_err_t Blink();
    static esp_err_t Ping();

    /**
     * 把小智（xiaozhi）的设备状态枚举映射成双眼状态字符串。
     * 返回值永不为空，未知状态回落 "IDLE"。
     */
    static const char* StateFromDeviceState(DeviceState state);

    /**
     * 把服务器下发的 emotion 字段映射成双眼状态字符串。
     * emotion 为 nullptr 或未知值时返回 "SPEAKING"（播报中的默认表情）。
     */
    static const char* StateFromEmotion(const char* emotion);

private:
    static esp_err_t SendCommand(const char* command);
};
