#pragma once

#include "esp_err.h"

class EyeUartLink final {
public:
    static esp_err_t Init();
    static esp_err_t SendState(const char* state);
    static esp_err_t SendGaze(int x_percent, int y_percent);
    static esp_err_t Blink();
    static esp_err_t Ping();

private:
    static esp_err_t SendCommand(const char* command);
};
