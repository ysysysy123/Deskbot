#include "eye_uart_link.h"

#include <cstdio>
#include <cstring>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {

constexpr uart_port_t kEyeUart = UART_NUM_1;
constexpr gpio_num_t kEyeTxPin = GPIO_NUM_43;
constexpr gpio_num_t kEyeRxPin = GPIO_NUM_44;
constexpr int kEyeBaud = 115200;
constexpr char kTag[] = "EyeUart";
bool initialized = false;
bool linked = false;
char current_state[16] = "IDLE";
portMUX_TYPE state_mux = portMUX_INITIALIZER_UNLOCKED;

void SaveCurrentState(const char* state)
{
    portENTER_CRITICAL(&state_mux);
    strncpy(current_state, state, sizeof(current_state) - 1);
    current_state[sizeof(current_state) - 1] = '\0';
    portEXIT_CRITICAL(&state_mux);
}

void ResendCurrentState()
{
    char state[sizeof(current_state)];
    portENTER_CRITICAL(&state_mux);
    memcpy(state, current_state, sizeof(state));
    portEXIT_CRITICAL(&state_mux);
    EyeUartLink::SendState(state);
}

void EyeUartReceiveTask(void*)
{
    char line[64];
    size_t length = 0;
    while (true) {
        uint8_t byte = 0;
        if (uart_read_bytes(kEyeUart, &byte, 1, pdMS_TO_TICKS(1000)) != 1) {
            if (!linked) {
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
                ESP_LOGI(kTag, "RX: %s", line);
                if (strncmp(line, "READY ", 6) == 0 ||
                    strncmp(line, "PONG ", 5) == 0) {
                    if (!linked) {
                        linked = true;
                        ESP_LOGI(kTag, "DualEye link established");
                    }
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
            ESP_LOGW(kTag, "RX line too long");
        }
    }
}

}  // namespace

esp_err_t EyeUartLink::Init()
{
    if (initialized) {
        return ESP_OK;
    }
    if (uart_is_driver_installed(kEyeUart)) {
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
        return result;
    }
    result = uart_set_pin(kEyeUart, kEyeTxPin, kEyeRxPin,
                          UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (result != ESP_OK) {
        return result;
    }
    result = uart_driver_install(kEyeUart, 256, 256, 0, nullptr, 0);
    if (result != ESP_OK) {
        return result;
    }
    if (xTaskCreate(EyeUartReceiveTask, "eye_uart_rx", 2048,
                    nullptr, 4, nullptr) != pdPASS) {
        uart_driver_delete(kEyeUart);
        return ESP_ERR_NO_MEM;
    }
    initialized = true;
    ESP_LOGI(kTag, "UART1 ready: TX GPIO43, RX GPIO44, 115200 8N1");
    return ESP_OK;
}

esp_err_t EyeUartLink::SendCommand(const char* command)
{
    if (!initialized) {
        return ESP_ERR_INVALID_STATE;
    }
    if (command == nullptr || command[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    const int command_bytes = uart_write_bytes(kEyeUart, command, strlen(command));
    if (command_bytes < 0) {
        return ESP_FAIL;
    }
    return uart_write_bytes(kEyeUart, "\n", 1) == 1 ? ESP_OK : ESP_FAIL;
}

esp_err_t EyeUartLink::SendState(const char* state)
{
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

esp_err_t EyeUartLink::SendGaze(int x_percent, int y_percent)
{
    if (x_percent < -100 || x_percent > 100 ||
        y_percent < -100 || y_percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    char command[32];
    snprintf(command, sizeof(command), "GAZE %d %d", x_percent, y_percent);
    return SendCommand(command);
}

esp_err_t EyeUartLink::Blink()
{
    return SendCommand("BLINK");
}

esp_err_t EyeUartLink::Ping()
{
    return SendCommand("PING");
}
