#ifndef QMI8658_H
#define QMI8658_H

#include <cstdint>
#include <driver/i2c_master.h>
#include <esp_err.h>

#include "i2c_device.h"

struct ImuData {
    float ax = 0.0f;  // Acceleration in g
    float ay = 0.0f;
    float az = 0.0f;
    float gx = 0.0f;  // Gyroscope in degrees per second (dps)
    float gy = 0.0f;
    float gz = 0.0f;
    float pitch = 0.0f;  // Tilt Pitch in degrees
    float roll = 0.0f;   // Tilt Roll in degrees
};

/** Driver for QMI8658 6-axis IMU (Accelerometer + Gyroscope) */
class Qmi8658 final : public I2cDevice {
public:
    static constexpr uint8_t kDefaultAddress = 0x6A;
    static constexpr uint8_t kAltAddress = 0x6B;

    static esp_err_t Probe(i2c_master_bus_handle_t i2c_bus, uint8_t address = kDefaultAddress,
                           int timeout_ms = 100);

    Qmi8658(i2c_master_bus_handle_t i2c_bus, uint8_t address = kDefaultAddress);

    esp_err_t Initialize();
    esp_err_t ReadData(ImuData& data);
    bool IsInitialized() const { return initialized_; }

private:
    bool initialized_ = false;
};

#endif  // QMI8658_H
