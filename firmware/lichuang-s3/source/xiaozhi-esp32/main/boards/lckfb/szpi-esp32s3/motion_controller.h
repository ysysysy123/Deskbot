#ifndef MOTION_CONTROLLER_H
#define MOTION_CONTROLLER_H

#include <cstdint>
#include <esp_err.h>

class McpServer;

/**
 * High-level abstract motion controller interface for Deskbot.
 * Serves as the single source of truth for all driving, steering,
 * safety timeouts, touch UI feedback, and MCP autonomous tools.
 */
class MotionController {
public:
    virtual ~MotionController() = default;

    // Direct wheel control (-100% .. 100%)
    virtual esp_err_t SetWheelSpeeds(int left_percent, int right_percent) = 0;
    virtual esp_err_t SetLeftSpeed(int percent) = 0;
    virtual esp_err_t SetRightSpeed(int percent) = 0;

    // High-level differential steering:
    // linear: -100..100 (forward/backward)
    // angular: -100..100 (turn right / turn left)
    virtual esp_err_t Drive(int linear_speed, int angular_turn) = 0;

    // Timed drive operations (with safety auto-stop)
    virtual esp_err_t DriveForDuration(int left_percent, int right_percent, int duration_ms) = 0;
    virtual esp_err_t TurnForDuration(int angular_turn, int duration_ms) = 0;

    // Convenient high-level actions (duration_ms > 0 enables auto-stop safety timer)
    inline esp_err_t Forward(int speed_percent = 20, int duration_ms = 0) {
        if (duration_ms > 0) return DriveForDuration(speed_percent, speed_percent, duration_ms);
        return SetWheelSpeeds(speed_percent, speed_percent);
    }
    inline esp_err_t Backward(int speed_percent = 20, int duration_ms = 0) {
        if (duration_ms > 0) return DriveForDuration(-speed_percent, -speed_percent, duration_ms);
        return SetWheelSpeeds(-speed_percent, -speed_percent);
    }
    inline esp_err_t TurnLeft(int speed_percent = 20, int duration_ms = 0) {
        if (duration_ms > 0) return DriveForDuration(-speed_percent, speed_percent, duration_ms);
        return SetWheelSpeeds(-speed_percent, speed_percent);
    }
    inline esp_err_t TurnRight(int speed_percent = 20, int duration_ms = 0) {
        if (duration_ms > 0) return DriveForDuration(speed_percent, -speed_percent, duration_ms);
        return SetWheelSpeeds(speed_percent, -speed_percent);
    }

    // Emergency stop
    virtual esp_err_t Stop() = 0;

    // Anthropomorphic gestures
    enum class MotionGesture {
        kNod,        // 点头/赞同 (前后点动两次)
        kWiggle,      // 欢快摇摆/撒娇 (快速左右摇摆三次)
        kConfused,    // 疑惑/思考 (歪头打量并短暂停顿)
        kShakeHead,   // 摇头/拒绝 (左右坚决摇头两次)
        kCheer,       // 庆祝/转圈 (原地顺时针欢呼转圈)
    };
    virtual esp_err_t PlayGesture(MotionGesture gesture) = 0;

    inline esp_err_t Nod() { return PlayGesture(MotionGesture::kNod); }
    inline esp_err_t Wiggle() { return PlayGesture(MotionGesture::kWiggle); }
    inline esp_err_t Confused() { return PlayGesture(MotionGesture::kConfused); }
    inline esp_err_t ShakeHead() { return PlayGesture(MotionGesture::kShakeHead); }
    inline esp_err_t Cheer() { return PlayGesture(MotionGesture::kCheer); }

    // State inspection (always returns actual hardware pulse and speed)
    virtual int GetLeftSpeed() const = 0;
    virtual int GetRightSpeed() const = 0;
    virtual uint16_t GetLeftPulseUs() const = 0;
    virtual uint16_t GetRightPulseUs() const = 0;
    virtual uint16_t GetNeutralPulseUs() const = 0;
    virtual uint16_t GetDeadbandUs() const = 0;

    // MCP registration for LLM agent integration
    virtual void RegisterMcpTools(McpServer& server) = 0;
};

#endif  // MOTION_CONTROLLER_H
