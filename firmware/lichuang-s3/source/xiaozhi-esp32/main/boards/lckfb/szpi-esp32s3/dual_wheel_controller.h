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
 * Incorporates measured deadband compensation ([1510..1610us], neutral = 1560us).
 */
class DualWheelController final {
public:
    static constexpr uint8_t kDefaultAddress = 0x40;
    static constexpr uint8_t kLeftChannel = 0;
    static constexpr uint8_t kRightChannel = 1;
    static constexpr uint16_t kPwmFrequencyHz = 50;

    // Measured hardware characteristics: deadband is [1510us, 1610us]
    static constexpr uint16_t kNeutralPulseUs = 1560;  // Center of [1510, 1610]
    static constexpr uint16_t kDeadbandHalfWidthUs = 50; // (1610 - 1510) / 2
    static constexpr uint16_t kActiveSpeedSpanUs = 400;  // 100% speed span outside deadband

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
    uint16_t GetLeftPulseUs() const { return current_left_pulse_us_; }
    uint16_t GetRightPulseUs() const { return current_right_pulse_us_; }

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
    uint16_t current_left_pulse_us_ = kNeutralPulseUs;
    uint16_t current_right_pulse_us_ = kNeutralPulseUs;
};

#endif  // DUAL_WHEEL_CONTROLLER_H
