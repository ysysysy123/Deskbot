#include "qmi8658.h"

#include <cmath>
#include <esp_log.h>

#define TAG "Qmi8658"

namespace {
constexpr uint8_t REG_WHO_AM_I = 0x00;
constexpr uint8_t REG_CTRL1 = 0x02;
constexpr uint8_t REG_CTRL2 = 0x03;
constexpr uint8_t REG_CTRL3 = 0x04;
constexpr uint8_t REG_CTRL5 = 0x06;
constexpr uint8_t REG_CTRL7 = 0x08;
constexpr uint8_t REG_AX_L  = 0x35;

constexpr uint8_t EXPECTED_WHO_AM_I = 0x05;

// ±4g scale -> 8192 LSB/g
constexpr float ACCEL_SCALE = 1.0f / 8192.0f;
// ±512 dps scale -> 64 LSB/dps
constexpr float GYRO_SCALE  = 1.0f / 64.0f;
}  // namespace

esp_err_t Qmi8658::Probe(i2c_master_bus_handle_t i2c_bus, uint8_t address, int timeout_ms) {
    if (i2c_bus == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_probe(i2c_bus, address, timeout_ms);
}

Qmi8658::Qmi8658(i2c_master_bus_handle_t i2c_bus, uint8_t address)
    : I2cDevice(i2c_bus, address) {}

esp_err_t Qmi8658::Initialize() {
    uint8_t chip_id = ReadReg(REG_WHO_AM_I);
    if (chip_id != EXPECTED_WHO_AM_I) {
        ESP_LOGW(TAG, "QMI8658 WHO_AM_I mismatch: expected 0x%02x, got 0x%02x",
                 EXPECTED_WHO_AM_I, chip_id);
        return ESP_ERR_NOT_FOUND;
    }

    // Auto-address increment enabled
    WriteReg(REG_CTRL1, 0x60);
    // Accel ±4g, 100Hz
    WriteReg(REG_CTRL2, 0x14);
    // Gyro ±512 dps, 100Hz
    WriteReg(REG_CTRL3, 0x54);
    // Enable low pass filter
    WriteReg(REG_CTRL5, 0x55);
    // Enable both Accel and Gyro
    WriteReg(REG_CTRL7, 0x03);

    initialized_ = true;
    ESP_LOGI(TAG, "QMI8658 6-axis IMU initialized successfully (ID: 0x%02x)", chip_id);
    return ESP_OK;
}

esp_err_t Qmi8658::ReadData(ImuData& data) {
    if (!initialized_) {
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t raw[12] = {0};
    ReadRegs(REG_AX_L, raw, sizeof(raw));

    int16_t raw_ax = static_cast<int16_t>(raw[0] | (raw[1] << 8));
    int16_t raw_ay = static_cast<int16_t>(raw[2] | (raw[3] << 8));
    int16_t raw_az = static_cast<int16_t>(raw[4] | (raw[5] << 8));

    int16_t raw_gx = static_cast<int16_t>(raw[6] | (raw[7] << 8));
    int16_t raw_gy = static_cast<int16_t>(raw[8] | (raw[9] << 8));
    int16_t raw_gz = static_cast<int16_t>(raw[10] | (raw[11] << 8));

    data.ax = static_cast<float>(raw_ax) * ACCEL_SCALE;
    data.ay = static_cast<float>(raw_ay) * ACCEL_SCALE;
    data.az = static_cast<float>(raw_az) * ACCEL_SCALE;

    data.gx = static_cast<float>(raw_gx) * GYRO_SCALE;
    data.gy = static_cast<float>(raw_gy) * GYRO_SCALE;
    data.gz = static_cast<float>(raw_gz) * GYRO_SCALE;

    // Estimate Pitch and Roll (in degrees)
    float ax2 = data.ax * data.ax;
    float ay2 = data.ay * data.ay;
    float az2 = data.az * data.az;
    data.pitch = std::atan2(data.ay, std::sqrt(ax2 + az2)) * (180.0f / 3.14159265f);
    data.roll  = std::atan2(-data.ax, std::sqrt(ay2 + az2)) * (180.0f / 3.14159265f);

    return ESP_OK;
}
