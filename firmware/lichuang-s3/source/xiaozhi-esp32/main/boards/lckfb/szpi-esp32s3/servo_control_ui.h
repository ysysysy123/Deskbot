#ifndef SERVO_CONTROL_UI_H
#define SERVO_CONTROL_UI_H

#include <lvgl.h>

class MotionController;
class Qmi8658;
class Esp32Camera;

/**
 * Multi-function debug and dashboard UI:
 * - Servo Control (speed % and real-time pulse width)
 * - Gyroscope Monitor (QMI8658 real-time accel, gyro, tilt)
 * - Camera Preview (GC2145 live image snap & inspection)
 */
class ServoControlUi {
public:
    ServoControlUi(MotionController* motion, Qmi8658* imu = nullptr, Esp32Camera* camera = nullptr);
    ~ServoControlUi();

    void SetupUI();

    void ShowServoPanel();
    void HideServoPanel();

    void ShowGyroPanel();
    void HideGyroPanel();

    void ShowCameraPanel();
    void HideCameraPanel();
    bool IsCamPanelVisible() const { return cam_panel_visible_; }

private:
    void CreateEntranceButtons();
    void CreateServoPanel();
    void CreateGyroPanel();
    void CreateCameraPanel();

    void UpdateServoLabels();
    void UpdateGyroLabels();
    void UpdateCameraPreview();

    static void OnEntranceServoClicked(lv_event_t* e);
    static void OnEntranceGyroClicked(lv_event_t* e);
    static void OnEntranceCamClicked(lv_event_t* e);

    static void OnBackFromServoClicked(lv_event_t* e);
    static void OnBackFromGyroClicked(lv_event_t* e);
    static void OnBackFromCamClicked(lv_event_t* e);

    static void OnLeftSpeedClicked(lv_event_t* e);
    static void OnRightSpeedClicked(lv_event_t* e);
    static void OnBothSpeedClicked(lv_event_t* e);
    static void OnStopAllClicked(lv_event_t* e);
    static void OnCameraSnapClicked(lv_event_t* e);

    static void OnPeriodicTimer(lv_timer_t* t);

    MotionController* motion_ = nullptr;
    Qmi8658* imu_ = nullptr;
    Esp32Camera* camera_ = nullptr;

    // Entrance buttons on chat screen
    lv_obj_t* btn_servo_ = nullptr;
    lv_obj_t* btn_gyro_ = nullptr;
    lv_obj_t* btn_cam_ = nullptr;

    // Panels
    lv_obj_t* panel_servo_ = nullptr;
    lv_obj_t* label_left_ = nullptr;
    lv_obj_t* label_right_ = nullptr;
    bool servo_panel_visible_ = false;

    lv_obj_t* panel_gyro_ = nullptr;
    lv_obj_t* label_gyro_status_ = nullptr;
    lv_obj_t* label_accel_ = nullptr;
    lv_obj_t* label_gyro_ = nullptr;
    lv_obj_t* label_tilt_ = nullptr;
    bool gyro_panel_visible_ = false;

    lv_obj_t* panel_cam_ = nullptr;
    lv_obj_t* img_preview_ = nullptr;
    lv_obj_t* label_cam_info_ = nullptr;
    bool cam_panel_visible_ = false;
    uint16_t* cam_preview_buf_ = nullptr;
    lv_image_dsc_t cam_img_dsc_{};

    lv_timer_t* refresh_timer_ = nullptr;
};

#endif  // SERVO_CONTROL_UI_H
