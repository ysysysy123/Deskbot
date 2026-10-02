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
