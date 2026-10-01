#include "dual_wheel_controller.h"

#include <algorithm>
#include <memory>

#include <esp_log.h>

#include "mcp_server.h"
#include "pca9685.h"

#define TAG "DualWheelController"

std::unique_ptr<DualWheelController> DualWheelController::Create(
    i2c_master_bus_handle_t i2c_bus, uint8_t address) {
    if (Pca9685::Probe(i2c_bus, address) != ESP_OK) {
        ESP_LOGW(TAG, "PCA9685 not found at I2C address 0x%02x", address);
        return nullptr;
    }
    return std::unique_ptr<DualWheelController>(new DualWheelController(i2c_bus, address));
}

DualWheelController::DualWheelController(i2c_master_bus_handle_t i2c_bus, uint8_t address)
    : pwm_(std::make_unique<Pca9685>(i2c_bus, address)) {}

DualWheelController::~DualWheelController() {
    if (stop_timer_ != nullptr) {
        esp_timer_stop(stop_timer_);
        esp_timer_delete(stop_timer_);
        stop_timer_ = nullptr;
    }
    if (initialized_) {
        Stop();
    }
}

bool DualWheelController::Initialize() {
    if (pwm_ == nullptr || pwm_->Initialize(kPwmFrequencyHz) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize PCA9685");
        return false;
    }

    const esp_timer_create_args_t timer_args = {
        .callback = &DualWheelController::OnDriveTimeout,
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "motion_stop",
        .skip_unhandled_events = true,
    };
    if (esp_timer_create(&timer_args, &stop_timer_) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create motion safety timer");
        return false;
    }

    initialized_ = true;
    if (Stop() != ESP_OK) {
        initialized_ = false;
        return false;
    }
    ESP_LOGI(TAG, "PCA9685 dual-wheel controller initialized at %uHz (Neutral: %uus, Deadband: ±%uus)",
             static_cast<unsigned>(kPwmFrequencyHz), kNeutralPulseUs, kDeadbandHalfWidthUs);
    return true;
}

void DualWheelController::OnDriveTimeout(void* arg) {
    auto* self = static_cast<DualWheelController*>(arg);
    if (self->Stop() != ESP_OK) {
        ESP_LOGW(TAG, "Failed to stop wheels after drive timeout");
    }
}

esp_err_t DualWheelController::SetWheelSpeed(uint8_t channel, int percent, bool reverse) {
    if (!initialized_ || pwm_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    if (percent < -100 || percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    if (reverse) {
        percent = -percent;
    }

    int pulse_us;
    if (percent == 0) {
        pulse_us = kNeutralPulseUs;
    } else if (percent > 0) {
        pulse_us = kNeutralPulseUs + kDeadbandHalfWidthUs +
                   (percent * static_cast<int>(kActiveSpeedSpanUs)) / 100;
    } else {
        pulse_us = kNeutralPulseUs - kDeadbandHalfWidthUs +
                   (percent * static_cast<int>(kActiveSpeedSpanUs)) / 100;
    }

    pulse_us = std::clamp(pulse_us, 900, 2200);

    if (channel == kLeftChannel) {
        current_left_pulse_us_ = static_cast<uint16_t>(pulse_us);
    } else {
        current_right_pulse_us_ = static_cast<uint16_t>(pulse_us);
    }

    return pwm_->SetChannelPulseUs(channel, static_cast<uint32_t>(pulse_us));
}

esp_err_t DualWheelController::SetLeftSpeed(int percent) {
    if (percent < -100 || percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    current_left_speed_ = percent;
    return SetWheelSpeed(kLeftChannel, percent, left_reverse_);
}

esp_err_t DualWheelController::SetRightSpeed(int percent) {
    if (percent < -100 || percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    current_right_speed_ = percent;
    return SetWheelSpeed(kRightChannel, percent, right_reverse_);
}

esp_err_t DualWheelController::SetWheelSpeeds(int left_percent, int right_percent) {
    if (left_percent < -100 || left_percent > 100 || right_percent < -100 ||
        right_percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    current_left_speed_ = left_percent;
    current_right_speed_ = right_percent;
    esp_err_t result = SetWheelSpeed(kLeftChannel, left_percent, left_reverse_);
    if (result != ESP_OK) {
        return result;
    }
    return SetWheelSpeed(kRightChannel, right_percent, right_reverse_);
}

esp_err_t DualWheelController::SetWheelSpeedsForDuration(int left_percent, int right_percent,
                                                          int duration_ms) {
    if (duration_ms <= 0 || duration_ms > kMaxDriveDurationMs) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t result = SetWheelSpeeds(left_percent, right_percent);
    if (result != ESP_OK) {
        return result;
    }
    if (stop_timer_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_timer_stop(stop_timer_);
    result = esp_timer_start_once(stop_timer_, static_cast<uint64_t>(duration_ms) * 1000ULL);
    if (result != ESP_OK) {
        Stop();
    }
    return result;
}

esp_err_t DualWheelController::Stop() {
    if (pwm_ == nullptr || !initialized_) {
        return ESP_ERR_INVALID_STATE;
    }
    if (stop_timer_ != nullptr) {
        esp_timer_stop(stop_timer_);
    }
    current_left_speed_ = 0;
    current_right_speed_ = 0;
    current_left_pulse_us_ = kNeutralPulseUs;
    current_right_pulse_us_ = kNeutralPulseUs;
    esp_err_t res1 = pwm_->SetChannelPulseUs(kLeftChannel, kNeutralPulseUs);
    esp_err_t res2 = pwm_->SetChannelPulseUs(kRightChannel, kNeutralPulseUs);
    return (res1 != ESP_OK) ? res1 : res2;
}

void DualWheelController::RegisterMcpTools(McpServer& server) {
    server.AddTool(
        "self.motion.drive",
        "Drive the two continuous-rotation wheels for a limited duration. Values are -100..100.",
        PropertyList({Property("left", kPropertyTypeInteger, -100, 100),
                      Property("right", kPropertyTypeInteger, -100, 100),
                      Property("duration_ms", kPropertyTypeInteger, kDefaultDriveDurationMs, 1,
                               kMaxDriveDurationMs)}),
        [this](const PropertyList& properties) -> ToolResult {
            const int left = properties["left"].value<int>();
            const int right = properties["right"].value<int>();
            const int duration_ms = properties["duration_ms"].value<int>();
            if (esp_err_t result = SetWheelSpeedsForDuration(left, right, duration_ms);
                result != ESP_OK) {
                return std::unexpected(esp_err_to_name(result));
            }
            return true;
        });

    server.AddTool("self.motion.stop", "Stop both wheels immediately", PropertyList(),
                   [this](const PropertyList&) -> ToolResult {
                       if (esp_err_t result = Stop(); result != ESP_OK) {
                           return std::unexpected(esp_err_to_name(result));
                       }
                       return true;
                   });
}
