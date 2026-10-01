#ifndef PCA9685_H
#define PCA9685_H

#include <cstdint>

#include <driver/i2c_master.h>
#include <esp_err.h>

#include "i2c_device.h"

/** Minimal PCA9685 PWM driver for servo/actuator outputs. */
class Pca9685 final : public I2cDevice {
public:
    static constexpr uint8_t kDefaultAddress = 0x40;
    static constexpr uint16_t kDefaultFrequencyHz = 50;
    static constexpr uint32_t kDefaultOscillatorHz = 25000000;

    static esp_err_t Probe(i2c_master_bus_handle_t i2c_bus, uint8_t address,
                           int timeout_ms = 100);

    Pca9685(i2c_master_bus_handle_t i2c_bus, uint8_t address = kDefaultAddress,
            uint32_t oscillator_hz = kDefaultOscillatorHz);

    esp_err_t Initialize(uint16_t frequency_hz = kDefaultFrequencyHz);
    esp_err_t SetPwm(uint8_t channel, uint16_t on_count, uint16_t off_count);
    esp_err_t SetChannelPulseUs(uint8_t channel, uint32_t pulse_us);
    esp_err_t SetAllChannelsOff();

    uint16_t frequency_hz() const { return frequency_hz_; }

private:
    uint32_t oscillator_hz_;
    uint16_t frequency_hz_ = 0;
};

#endif  // PCA9685_H
