#include "pca9685.h"

#include <algorithm>
#include <cmath>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

namespace {
constexpr uint8_t kMode1Register = 0x00;
constexpr uint8_t kMode2Register = 0x01;
constexpr uint8_t kLed0OnLowRegister = 0x06;
constexpr uint8_t kAllLedOffLowRegister = 0xFA;
constexpr uint8_t kPrescaleRegister = 0xFE;
constexpr uint8_t kMode1Restart = 1 << 7;
constexpr uint8_t kMode1AutoIncrement = 1 << 5;
constexpr uint8_t kMode1Sleep = 1 << 4;
constexpr uint8_t kMode2OutputDriver = 1 << 2;
constexpr uint8_t kFullOff = 1 << 4;
constexpr uint8_t kChannelCount = 16;
constexpr uint16_t kPwmResolution = 4096;
}  // namespace

esp_err_t Pca9685::Probe(i2c_master_bus_handle_t i2c_bus, uint8_t address, int timeout_ms) {
    if (i2c_bus == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_probe(i2c_bus, address, timeout_ms);
}

Pca9685::Pca9685(i2c_master_bus_handle_t i2c_bus, uint8_t address, uint32_t oscillator_hz)
    : I2cDevice(i2c_bus, address), oscillator_hz_(oscillator_hz) {}

esp_err_t Pca9685::Initialize(uint16_t frequency_hz) {
    if (frequency_hz == 0 || oscillator_hz_ == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    const double prescale_value =
        (static_cast<double>(oscillator_hz_) / kPwmResolution / frequency_hz) - 1.0;
    const auto prescale = static_cast<uint8_t>(std::clamp(std::lround(prescale_value), 3L, 255L));

    WriteReg(kMode1Register, kMode1Sleep | kMode1AutoIncrement);
    WriteReg(kPrescaleRegister, prescale);
    WriteReg(kMode1Register, kMode1Restart | kMode1AutoIncrement);
    vTaskDelay(pdMS_TO_TICKS(1));
    WriteReg(kMode2Register, kMode2OutputDriver);
    frequency_hz_ = frequency_hz;
    return SetAllChannelsOff();
}

esp_err_t Pca9685::SetPwm(uint8_t channel, uint16_t on_count, uint16_t off_count) {
    if (channel >= kChannelCount || on_count >= kPwmResolution || off_count >= kPwmResolution) {
        return ESP_ERR_INVALID_ARG;
    }

    const uint8_t data[4] = {
        static_cast<uint8_t>(on_count & 0xff),
        static_cast<uint8_t>((on_count >> 8) & 0x0f),
        static_cast<uint8_t>(off_count & 0xff),
        static_cast<uint8_t>((off_count >> 8) & 0x0f),
    };
    WriteRegs(kLed0OnLowRegister + channel * 4, data, sizeof(data));
    return ESP_OK;
}

esp_err_t Pca9685::SetChannelPulseUs(uint8_t channel, uint32_t pulse_us) {
    if (frequency_hz_ == 0) {
        return ESP_ERR_INVALID_STATE;
    }

    const uint32_t period_us = 1000000UL / frequency_hz_;
    const uint32_t bounded_pulse_us = std::min(pulse_us, period_us);
    const uint16_t off_count = static_cast<uint16_t>(std::min(
        static_cast<uint32_t>(kPwmResolution - 1),
        (bounded_pulse_us * kPwmResolution * frequency_hz_) / 1000000UL));
    return SetPwm(channel, 0, off_count);
}

esp_err_t Pca9685::SetAllChannelsOff() {
    const uint8_t data[4] = {0, 0, 0, kFullOff};
    WriteRegs(kAllLedOffLowRegister, data, sizeof(data));
    return ESP_OK;
}
