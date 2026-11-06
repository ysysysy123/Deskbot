#include "eye_uart_link.h"

#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>

#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {

// ===== 立创实战派 ESP32-S3 的双眼串口 =====
// J2-2 = GPIO10 = TX  →  双眼 P2-9  UART_RXD
// J2-1 = GPIO11 = RX  ←  双眼 P2-10 UART_TXD
constexpr uart_port_t kEyeUart = UART_NUM_1;
constexpr int kEyeTxPin = 10;
constexpr int kEyeRxPin = 11;
constexpr int kEyeBaud = 115200;

constexpr char kTag[] = "EyeUart";

std::atomic<bool> g_initialized{false};
std::atomic<bool> g_linked{false};
std::atomic<bool> g_verbose{true};
std::mutex g_tx_mutex;

char g_current_state[16] = "IDLE";
portMUX_TYPE g_state_mux = portMUX_INITIALIZER_UNLOCKED;

void SaveCurrentState(const char* state) {
    portENTER_CRITICAL(&g_state_mux);
    strncpy(g_current_state, state, sizeof(g_current_state) - 1);
    g_current_state[sizeof(g_current_state) - 1] = '\0';
    portEXIT_CRITICAL(&g_state_mux);
}

void LoadCurrentState(char* out, size_t out_len) {
    portENTER_CRITICAL(&g_state_mux);
    strncpy(out, g_current_state, out_len - 1);
    out[out_len - 1] = '\0';
    portEXIT_CRITICAL(&g_state_mux);
}

void ResendCurrentState() {
    char state[sizeof(g_current_state)];
    LoadCurrentState(state, sizeof(state));
    EyeUartLink::SendState(state);
}

/**
 * 接收任务：逐字节组行，处理 READY / PONG。
 * 未建立链路时每 1 秒发一次 PING 重试（双眼先开、主控先开、双眼单独重启都能自愈）。
 */
void EyeUartReceiveTask(void*) {
    char line[64];
    size_t length = 0;

    while (true) {
        uint8_t byte = 0;
        const int read = uart_read_bytes(kEyeUart, &byte, 1, pdMS_TO_TICKS(1000));

        if (read != 1) {
            // 超时：链路未建立则重试握手
            if (!g_linked.load()) {
                EyeUartLink::Ping();
            }
            continue;
        }

        if (byte == '\r') {
            continue;
        }

        if (byte == '\n') {
            if (length > 0) {
                line[length] = '\0';
                if (g_verbose.load()) {
                    ESP_LOGI(kTag, "RX: %s", line);
                }
                const bool ready = (strncmp(line, "READY ", 6) == 0);
                const bool pong = (strncmp(line, "PONG ", 5) == 0);
                if (ready || pong) {
                    if (!g_linked.load()) {
                        g_linked.store(true);
                        ESP_LOGI(kTag, "DualEye link established (%s)", ready ? "READY" : "PONG");
                    }
                    // 握手成功后重发当前状态，保证两板上电顺序无关
                    ResendCurrentState();
                }
                length = 0;
            }
            continue;
        }

        if (length + 1 < sizeof(line)) {
            line[length++] = static_cast<char>(byte);
        } else {
            length = 0;
            ESP_LOGW(kTag, "RX line too long, dropped");
        }
    }
}

}  // namespace

esp_err_t EyeUartLink::Init() {
    if (g_initialized.load()) {
        return ESP_OK;
    }
    if (uart_is_driver_installed(kEyeUart)) {
        ESP_LOGW(kTag, "UART%d already installed", static_cast<int>(kEyeUart));
        return ESP_ERR_INVALID_STATE;
    }

    uart_config_t config = {};
    config.baud_rate = kEyeBaud;
    config.data_bits = UART_DATA_8_BITS;
    config.parity = UART_PARITY_DISABLE;
    config.stop_bits = UART_STOP_BITS_1;
    config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    config.source_clk = UART_SCLK_DEFAULT;

    esp_err_t result = uart_param_config(kEyeUart, &config);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "uart_param_config failed: %s", esp_err_to_name(result));
        return result;
    }

    // 通过 GPIO Matrix 路由到 J2-1 / J2-2，不影响 USB Serial/JTAG 日志
    result = uart_set_pin(kEyeUart, kEyeTxPin, kEyeRxPin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "uart_set_pin failed: %s", esp_err_to_name(result));
        return result;
    }

    result = uart_driver_install(kEyeUart, 256, 256, 0, nullptr, 0);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "uart_driver_install failed: %s", esp_err_to_name(result));
        return result;
    }

    if (xTaskCreate(EyeUartReceiveTask, "eye_uart_rx", 2560, nullptr, 4, nullptr) != pdPASS) {
        uart_driver_delete(kEyeUart);
        return ESP_ERR_NO_MEM;
    }

    g_initialized.store(true);
    ESP_LOGI(kTag, "UART%d ready: TX GPIO%d, RX GPIO%d, %d 8N1", static_cast<int>(kEyeUart),
             kEyeTxPin, kEyeRxPin, kEyeBaud);
    return ESP_OK;
}

bool EyeUartLink::IsLinked() { return g_linked.load(); }

void EyeUartLink::SetVerbose(bool enable) { g_verbose.store(enable); }

esp_err_t EyeUartLink::SendCommand(const char* command) {
    if (!g_initialized.load()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (command == nullptr || command[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    std::lock_guard<std::mutex> lock(g_tx_mutex);
    const int written = uart_write_bytes(kEyeUart, command, strlen(command));
    if (written < 0) {
        return ESP_FAIL;
    }
    return uart_write_bytes(kEyeUart, "\n", 1) == 1 ? ESP_OK : ESP_FAIL;
}

esp_err_t EyeUartLink::SendState(const char* state) {
    if (state == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    SaveCurrentState(state);

    char command[32];
    const int written = snprintf(command, sizeof(command), "STATE %s", state);
    if (written < 0 || written >= static_cast<int>(sizeof(command))) {
        return ESP_ERR_INVALID_SIZE;
    }
    return SendCommand(command);
}

esp_err_t EyeUartLink::SendGaze(int x_percent, int y_percent) {
    if (x_percent < -100 || x_percent > 100 || y_percent < -100 || y_percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    char command[32];
    snprintf(command, sizeof(command), "GAZE %d %d", x_percent, y_percent);
    return SendCommand(command);
}

esp_err_t EyeUartLink::Blink() { return SendCommand("BLINK"); }

esp_err_t EyeUartLink::Ping() { return SendCommand("PING"); }

const char* EyeUartLink::StateFromDeviceState(DeviceState state) {
    switch (state) {
        case kDeviceStateListening:
            return "LISTENING";
        case kDeviceStateSpeaking:
            return "SPEAKING";
        case kDeviceStateFatalError:
            return "SLEEPING";
        case kDeviceStateNotifying:
            return "HAPPY";  // 通知/提醒用开心表情（可按产品需要调整）
        case kDeviceStateIdle:
        case kDeviceStateUnknown:
            return "IDLE";
        case kDeviceStateStarting:
        case kDeviceStateWifiConfiguring:
        case kDeviceStateConnecting:
        case kDeviceStateUpgrading:
        case kDeviceStateActivating:
        case kDeviceStateAudioTesting:
            return "THINKING";
    }
    return "IDLE";
}

const char* EyeUartLink::StateFromEmotion(const char* emotion) {
    if (emotion == nullptr || emotion[0] == '\0') {
        return "SPEAKING";
    }
    if (strcmp(emotion, "happy") == 0 || strcmp(emotion, "laughing") == 0 ||
        strcmp(emotion, "funny") == 0 || strcmp(emotion, "silly") == 0 ||
        strcmp(emotion, "delicious") == 0 || strcmp(emotion, "loving") == 0 ||
        strcmp(emotion, "kissy") == 0) {
        return "HAPPY";
    }
    if (strcmp(emotion, "sad") == 0 || strcmp(emotion, "crying") == 0) {
        return "SAD";
    }
    if (strcmp(emotion, "angry") == 0) {
        return "ANGRY";
    }
    if (strcmp(emotion, "surprised") == 0 || strcmp(emotion, "shocked") == 0 ||
        strcmp(emotion, "embarrassed") == 0) {
        return "SURPRISED";
    }
    if (strcmp(emotion, "thinking") == 0 || strcmp(emotion, "confused") == 0) {
        return "THINKING";
    }
    if (strcmp(emotion, "sleepy") == 0) {
        return "SLEEPING";
    }
    // neutral / relaxed / cool / confident / winking / 未知 → 播报中的默认表情
    return "SPEAKING";
}
