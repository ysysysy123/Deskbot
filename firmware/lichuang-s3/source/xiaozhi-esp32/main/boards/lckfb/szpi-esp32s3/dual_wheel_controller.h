#ifndef DUAL_WHEEL_CONTROLLER_H
#define DUAL_WHEEL_CONTROLLER_H

#include <cstdint>
#include <memory>

#include <driver/i2c_master.h>
#include <esp_err.h>
#include <esp_timer.h>

class McpServer;
class Pca9685;

/**
 * Two-channel controller for continuous-rotation servos connected to PCA9685.
 * Speed is expressed as a signed percentage: -100..100.
 */
class DualWheelController final {
public:
    static constexpr uint8_t kDefaultAddress = 0x40;
    static constexpr uint8_t kLeftChannel = 0;
    static constexpr uint8_t kRightChannel = 1;
    static constexpr uint16_t kPwmFrequencyHz = 50;
    static constexpr uint16_t kNeutralPulseUs = 1500;
    static constexpr uint16_t kSpeedRangeUs = 500;
    static constexpr int kDefaultDriveDurationMs = 500;
    static constexpr int kMaxDriveDurationMs = 5000;

    static std::unique_ptr<DualWheelController> Create(i2c_master_bus_handle_t i2c_bus,
                                                        uint8_t address = kDefaultAddress);

    ~DualWheelController();

    bool Initialize();
    esp_err_t SetWheelSpeeds(int left_percent, int right_percent);
    esp_err_t SetWheelSpeedsForDuration(int left_percent, int right_percent,
                                         int duration_ms);
    esp_err_t SetLeftSpeed(int percent);
    esp_err_t SetRightSpeed(int percent);
    int GetLeftSpeed() const { return current_left_speed_; }
    int GetRightSpeed() const { return current_right_speed_; }
    esp_err_t Stop();
    void RegisterMcpTools(McpServer& server);

private:
    DualWheelController(i2c_master_bus_handle_t i2c_bus, uint8_t address);
    static void OnDriveTimeout(void* arg);
    esp_err_t SetWheelSpeed(uint8_t channel, int percent, bool reverse);

    std::unique_ptr<Pca9685> pwm_;
    esp_timer_handle_t stop_timer_ = nullptr;
    bool initialized_ = false;
    bool left_reverse_ = false;
    bool right_reverse_ = true;
    int current_left_speed_ = 0;
    int current_right_speed_ = 0;
};

#endif  // DUAL_WHEEL_CONTROLLER_H
