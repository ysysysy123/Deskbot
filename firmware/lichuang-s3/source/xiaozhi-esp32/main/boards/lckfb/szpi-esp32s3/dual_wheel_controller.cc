#include "dual_wheel_controller.h"

#include <algorithm>
#include <memory>
#include <sstream>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
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
    if (stop_timer_ != nullptr) {
        esp_timer_stop(stop_timer_);
    }
    current_left_speed_ = percent;
    return SetWheelSpeed(kLeftChannel, percent, left_reverse_);
}

esp_err_t DualWheelController::SetRightSpeed(int percent) {
    if (percent < -100 || percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    if (stop_timer_ != nullptr) {
        esp_timer_stop(stop_timer_);
    }
    current_right_speed_ = percent;
    return SetWheelSpeed(kRightChannel, percent, right_reverse_);
}

esp_err_t DualWheelController::SetWheelSpeeds(int left_percent, int right_percent) {
    if (left_percent < -100 || left_percent > 100 || right_percent < -100 ||
        right_percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    if (stop_timer_ != nullptr) {
        esp_timer_stop(stop_timer_);
    }
    current_left_speed_ = left_percent;
    current_right_speed_ = right_percent;
    esp_err_t result = SetWheelSpeed(kLeftChannel, left_percent, left_reverse_);
    if (result != ESP_OK) {
        return result;
    }
    return SetWheelSpeed(kRightChannel, right_percent, right_reverse_);
}

esp_err_t DualWheelController::Drive(int linear_speed, int angular_turn) {
    linear_speed = std::clamp(linear_speed, -100, 100);
    angular_turn = std::clamp(angular_turn, -100, 100);
    int left = std::clamp(linear_speed + angular_turn, -100, 100);
    int right = std::clamp(linear_speed - angular_turn, -100, 100);
    return SetWheelSpeeds(left, right);
}

esp_err_t DualWheelController::DriveForDuration(int left_percent, int right_percent,
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

esp_err_t DualWheelController::TurnForDuration(int angular_turn, int duration_ms) {
    angular_turn = std::clamp(angular_turn, -100, 100);
    return DriveForDuration(angular_turn, -angular_turn, duration_ms);
}

esp_err_t DualWheelController::Stop() {
    cancel_gesture_.store(true);
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

esp_err_t DualWheelController::PlayGesture(MotionGesture gesture) {
    if (!initialized_ || pwm_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    if (stop_timer_ != nullptr) {
        esp_timer_stop(stop_timer_);
    }
    cancel_gesture_.store(true);
    if (is_gesturing_.load()) {
        vTaskDelay(pdMS_TO_TICKS(40));
    }
    cancel_gesture_.store(false);

    struct GestureArg {
        DualWheelController* self;
        MotionGesture gesture;
    };
    auto* arg = new GestureArg{this, gesture};

    BaseType_t res = xTaskCreate([](void* p) {
        auto* a = static_cast<GestureArg*>(p);
        a->self->ExecuteGestureSequence(a->gesture);
        delete a;
        vTaskDelete(nullptr);
    }, "motion_gesture", 2560, arg, 4, nullptr);

    return (res == pdPASS) ? ESP_OK : ESP_FAIL;
}

void DualWheelController::ExecuteGestureSequence(MotionGesture gesture) {
    is_gesturing_.store(true);
    ESP_LOGI(TAG, "Playing gesture %d", static_cast<int>(gesture));

    auto delay_or_cancel = [this](int ms) -> bool {
        int steps = ms / 20;
        for (int i = 0; i < steps; ++i) {
            if (cancel_gesture_.load()) return false;
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        return !cancel_gesture_.load();
    };

    switch (gesture) {
        case MotionGesture::kNod:
            // 点头：向前轻探 -> 刹车 -> 后退回位，重复 2 次
            for (int i = 0; i < 2; ++i) {
                SetWheelSpeeds(20, 20);
                if (!delay_or_cancel(140)) break;
                SetWheelSpeeds(0, 0);
                if (!delay_or_cancel(60)) break;
                SetWheelSpeeds(-20, -20);
                if (!delay_or_cancel(140)) break;
                SetWheelSpeeds(0, 0);
                if (!delay_or_cancel(80)) break;
            }
            break;

        case MotionGesture::kWiggle:
            // 欢快摇摆：左摇 -> 右摇，连续 3 个来回
            for (int i = 0; i < 3; ++i) {
                SetWheelSpeeds(-25, 25);
                if (!delay_or_cancel(110)) break;
                SetWheelSpeeds(25, -25);
                if (!delay_or_cancel(110)) break;
            }
            break;

        case MotionGesture::kConfused:
            // 疑惑/歪头：左偏 -> 停顿思考 450ms -> 慢速回正
            SetWheelSpeeds(-20, 20);
            if (!delay_or_cancel(170)) break;
            SetWheelSpeeds(0, 0);
            if (!delay_or_cancel(450)) break;
            SetWheelSpeeds(18, -18);
            if (!delay_or_cancel(190)) break;
            break;

        case MotionGesture::kShakeHead:
            // 坚定摇头：左右甩头 2 次
            for (int i = 0; i < 2; ++i) {
                SetWheelSpeeds(-30, 30);
                if (!delay_or_cancel(180)) break;
                SetWheelSpeeds(30, -30);
                if (!delay_or_cancel(180)) break;
            }
            break;

        case MotionGesture::kCheer:
            // 旋转欢呼：原地自转一圈
            SetWheelSpeeds(30, -30);
            delay_or_cancel(750);
            break;
    }

    Stop();
    is_gesturing_.store(false);
    ESP_LOGI(TAG, "Gesture completed");
}

void DualWheelController::RegisterMcpTools(McpServer& server) {
    server.AddTool(
        "self.motion.drive",
        "Drive Deskbot with linear speed (-100..100) and turn angle (-100..100). If duration_ms > 0, auto-stops after duration; if duration_ms == 0, drives continuously until stop.",
        PropertyList({Property("linear", kPropertyTypeInteger, 0, -100, 100),
                      Property("angular", kPropertyTypeInteger, 0, -100, 100),
                      Property("duration_ms", kPropertyTypeInteger, kDefaultDriveDurationMs, 0,
                               kMaxDriveDurationMs)}),
        [this](const PropertyList& properties) -> ToolResult {
            const int linear = properties["linear"].value<int>();
            const int angular = properties["angular"].value<int>();
            const int duration_ms = properties["duration_ms"].value<int>();
            int left = std::clamp(linear + angular, -100, 100);
            int right = std::clamp(linear - angular, -100, 100);
            if (duration_ms > 0) {
                if (esp_err_t result = DriveForDuration(left, right, duration_ms);
                    result != ESP_OK) {
                    return std::unexpected(esp_err_to_name(result));
                }
            } else {
                if (esp_err_t result = SetWheelSpeeds(left, right); result != ESP_OK) {
                    return std::unexpected(esp_err_to_name(result));
                }
            }
            return true;
        });

    server.AddTool(
        "self.motion.forward_continuous",
        "Drive Deskbot forward continuously at specified speed percentage (default 20%). Call self.motion.stop to halt.",
        PropertyList({Property("speed", kPropertyTypeInteger, 20, 1, 100)}),
        [this](const PropertyList& properties) -> ToolResult {
            const int speed = properties["speed"].value<int>();
            if (esp_err_t result = Forward(speed, 0); result != ESP_OK) {
                return std::unexpected(esp_err_to_name(result));
            }
            return true;
        });

    server.AddTool(
        "self.motion.turn",
        "Turn Deskbot in place with angular speed (-100..100, positive=right, negative=left).",
        PropertyList({Property("angular", kPropertyTypeInteger, 25, -100, 100),
                      Property("duration_ms", kPropertyTypeInteger, 500, 1, kMaxDriveDurationMs)}),
        [this](const PropertyList& properties) -> ToolResult {
            const int angular = properties["angular"].value<int>();
            const int duration_ms = properties["duration_ms"].value<int>();
            if (esp_err_t result = TurnForDuration(angular, duration_ms); result != ESP_OK) {
                return std::unexpected(esp_err_to_name(result));
            }
            return true;
        });

    server.AddTool("self.motion.nod",
                   "Nod forward and backward to express agreement, affirmation, greeting, or acknowledgement.",
                   PropertyList(), [this](const PropertyList&) -> ToolResult {
                       if (esp_err_t result = PlayGesture(MotionGesture::kNod); result != ESP_OK) {
                           return std::unexpected(esp_err_to_name(result));
                       }
                       return true;
                   });

    server.AddTool("self.motion.wiggle",
                   "Wiggle body left and right quickly to express joy, happiness, excitement, or playful affection.",
                   PropertyList(), [this](const PropertyList&) -> ToolResult {
                       if (esp_err_t result = PlayGesture(MotionGesture::kWiggle); result != ESP_OK) {
                           return std::unexpected(esp_err_to_name(result));
                       }
                       return true;
                   });

    server.AddTool("self.motion.confused",
                   "Tilt to one side and pause briefly to express confusion, curiosity, thinking, or questioning.",
                   PropertyList(), [this](const PropertyList&) -> ToolResult {
                       if (esp_err_t result = PlayGesture(MotionGesture::kConfused); result != ESP_OK) {
                           return std::unexpected(esp_err_to_name(result));
                       }
                       return true;
                   });

    server.AddTool("self.motion.shake_head",
                   "Shake head firmly left and right to express disagreement, refusal, or negation.",
                   PropertyList(), [this](const PropertyList&) -> ToolResult {
                       if (esp_err_t result = PlayGesture(MotionGesture::kShakeHead); result != ESP_OK) {
                           return std::unexpected(esp_err_to_name(result));
                       }
                       return true;
                   });

    server.AddTool(
        "self.motion.gesture",
        "Perform an anthropomorphic robot gesture. Supported types: 'nod', 'wiggle', 'confused', 'shake_head', 'cheer'.",
        PropertyList({Property("name", kPropertyTypeString, "nod")}),
        [this](const PropertyList& properties) -> ToolResult {
            std::string name = properties["name"].value<std::string>();
            MotionGesture g = MotionGesture::kNod;
            if (name == "nod") g = MotionGesture::kNod;
            else if (name == "wiggle") g = MotionGesture::kWiggle;
            else if (name == "confused") g = MotionGesture::kConfused;
            else if (name == "shake_head" || name == "shake") g = MotionGesture::kShakeHead;
            else if (name == "cheer" || name == "spin") g = MotionGesture::kCheer;
            else return std::unexpected("Unknown gesture name. Supported: nod, wiggle, confused, shake_head, cheer");

            if (esp_err_t result = PlayGesture(g); result != ESP_OK) {
                return std::unexpected(esp_err_to_name(result));
            }
            return true;
        });

    server.AddTool("self.motion.stop", "Stop all wheels immediately", PropertyList(),
                   [this](const PropertyList&) -> ToolResult {
                       if (esp_err_t result = Stop(); result != ESP_OK) {
                           return std::unexpected(esp_err_to_name(result));
                       }
                       return true;
                   });

    server.AddTool("self.motion.get_status", "Get live hardware pulse and speed for both wheels",
                   PropertyList(), [this](const PropertyList&) -> ToolResult {
                       std::ostringstream ss;
                       ss << "{\"left_speed\": " << GetLeftSpeed()
                          << ", \"right_speed\": " << GetRightSpeed()
                          << ", \"left_pulse_us\": " << GetLeftPulseUs()
                          << ", \"right_pulse_us\": " << GetRightPulseUs()
                          << ", \"neutral_us\": " << GetNeutralPulseUs() << "}";
                       return ss.str();
                   });
}
