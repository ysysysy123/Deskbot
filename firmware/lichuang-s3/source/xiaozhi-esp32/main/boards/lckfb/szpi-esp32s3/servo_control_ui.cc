#include "servo_control_ui.h"

#include <cstdio>
#include <esp_log.h>

#include "esp32_camera.h"
#include "motion_controller.h"
#include "qmi8658.h"

#define TAG "ServoControlUi"

namespace {
lv_obj_t* CreateStyledButton(lv_obj_t* parent, int w, int h, uint32_t color_hex,
                             const char* text, int user_value, lv_event_cb_t cb, void* user_data) {
    lv_obj_t* btn = lv_button_create(parent);
    lv_obj_set_size(btn, w, h);
    lv_obj_set_style_bg_color(btn, lv_color_hex(color_hex), 0);
    lv_obj_set_style_radius(btn, 6, 0);
    lv_obj_set_user_data(btn, (void*)(intptr_t)user_value);

    lv_obj_t* lbl = lv_label_create(btn);
    lv_label_set_text(lbl, text);
    lv_obj_center(lbl);

    lv_obj_add_event_cb(btn, cb, LV_EVENT_CLICKED, user_data);
    return btn;
}

static lv_image_dsc_t s_cam_dsc;
}  // namespace

ServoControlUi::ServoControlUi(MotionController* motion, Qmi8658* imu, Esp32Camera* camera)
    : motion_(motion), imu_(imu), camera_(camera) {}

ServoControlUi::~ServoControlUi() {
    if (refresh_timer_ != nullptr) {
        lv_timer_delete(refresh_timer_);
        refresh_timer_ = nullptr;
    }
    if (panel_servo_ != nullptr) {
        lv_obj_delete(panel_servo_);
        panel_servo_ = nullptr;
    }
    if (panel_gyro_ != nullptr) {
        lv_obj_delete(panel_gyro_);
        panel_gyro_ = nullptr;
    }
    if (panel_cam_ != nullptr) {
        lv_obj_delete(panel_cam_);
        panel_cam_ = nullptr;
    }
    if (btn_servo_ != nullptr) {
        lv_obj_delete(btn_servo_);
        btn_servo_ = nullptr;
    }
    if (btn_gyro_ != nullptr) {
        lv_obj_delete(btn_gyro_);
        btn_gyro_ = nullptr;
    }
    if (btn_cam_ != nullptr) {
        lv_obj_delete(btn_cam_);
        btn_cam_ = nullptr;
    }
}

void ServoControlUi::SetupUI() {
    CreateEntranceButtons();
    CreateServoPanel();
    CreateGyroPanel();
    CreateCameraPanel();

    refresh_timer_ = lv_timer_create(OnPeriodicTimer, 150, this);
    ESP_LOGI(TAG, "Multi-function Dashboard UI (Servo, Gyro, Cam) initialized successfully");
}

void ServoControlUi::CreateEntranceButtons() {
    lv_obj_t* screen = lv_screen_active();

    // 1. [SERVO] Button
    btn_servo_ = CreateStyledButton(screen, 62, 26, 0x2563EB, "SERVO", 0,
                                    OnEntranceServoClicked, this);
    lv_obj_align(btn_servo_, LV_ALIGN_TOP_LEFT, 6, 26);

    // 2. [GYRO] Button
    btn_gyro_ = CreateStyledButton(screen, 62, 26, 0x0D9488, "GYRO", 0,
                                   OnEntranceGyroClicked, this);
    lv_obj_align(btn_gyro_, LV_ALIGN_TOP_LEFT, 72, 26);

    // 3. [CAM] Button
    btn_cam_ = CreateStyledButton(screen, 62, 26, 0x7C3AED, "CAM", 0,
                                  OnEntranceCamClicked, this);
    lv_obj_align(btn_cam_, LV_ALIGN_TOP_LEFT, 138, 26);
}

void ServoControlUi::CreateServoPanel() {
    lv_obj_t* screen = lv_screen_active();
    panel_servo_ = lv_obj_create(screen);
    lv_obj_set_size(panel_servo_, 320, 240);
    lv_obj_set_pos(panel_servo_, 0, 0);
    lv_obj_set_style_bg_color(panel_servo_, lv_color_hex(0x181825), 0);
    lv_obj_set_style_border_width(panel_servo_, 0, 0);
    lv_obj_set_style_pad_all(panel_servo_, 4, 0);
    lv_obj_set_scrollbar_mode(panel_servo_, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(panel_servo_, LV_OBJ_FLAG_HIDDEN);

    // Title label
    lv_obj_t* title = lv_label_create(panel_servo_);
    lv_label_set_text(title, "DESKBOT SERVO CONTROL");
    lv_obj_set_style_text_color(title, lv_color_hex(0x89DCEB), 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 8, 4);

    // Header STOP ALL button
    CreateStyledButton(panel_servo_, 84, 22, 0xEF4444, "STOP ALL", 0, OnStopAllClicked, this);
    lv_obj_align(lv_obj_get_child(panel_servo_, lv_obj_get_child_count(panel_servo_) - 1),
                 LV_ALIGN_TOP_RIGHT, -6, 2);

    // Left Servo (CH0) label
    label_left_ = lv_label_create(panel_servo_);
    lv_obj_set_style_text_color(label_left_, lv_color_hex(0xF9E2AF), 0);
    lv_obj_align(label_left_, LV_ALIGN_TOP_LEFT, 8, 28);

    // Left Servo buttons row
    lv_obj_t* row_l = lv_obj_create(panel_servo_);
    lv_obj_set_size(row_l, 312, 32);
    lv_obj_align(row_l, LV_ALIGN_TOP_MID, 0, 48);
    lv_obj_set_style_bg_opa(row_l, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row_l, 0, 0);
    lv_obj_set_style_pad_all(row_l, 0, 0);
    lv_obj_set_flex_flow(row_l, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row_l, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);

    CreateStyledButton(row_l, 54, 30, 0xF97316, "-30%", -30, OnLeftSpeedClicked, this);
    CreateStyledButton(row_l, 54, 30, 0xEA580C, "-10%", -10, OnLeftSpeedClicked, this);
    CreateStyledButton(row_l, 54, 30, 0x64748B, "STOP", 0, OnLeftSpeedClicked, this);
    CreateStyledButton(row_l, 54, 30, 0x0D9488, "+10%", 10, OnLeftSpeedClicked, this);
    CreateStyledButton(row_l, 54, 30, 0x14B8A6, "+30%", 30, OnLeftSpeedClicked, this);

    // Right Servo (CH1) label
    label_right_ = lv_label_create(panel_servo_);
    lv_obj_set_style_text_color(label_right_, lv_color_hex(0xA6E3A1), 0);
    lv_obj_align(label_right_, LV_ALIGN_TOP_LEFT, 8, 82);

    // Right Servo buttons row
    lv_obj_t* row_r = lv_obj_create(panel_servo_);
    lv_obj_set_size(row_r, 312, 32);
    lv_obj_align(row_r, LV_ALIGN_TOP_MID, 0, 102);
    lv_obj_set_style_bg_opa(row_r, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row_r, 0, 0);
    lv_obj_set_style_pad_all(row_r, 0, 0);
    lv_obj_set_flex_flow(row_r, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row_r, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);

    CreateStyledButton(row_r, 54, 30, 0xF97316, "-30%", -30, OnRightSpeedClicked, this);
    CreateStyledButton(row_r, 54, 30, 0xEA580C, "-10%", -10, OnRightSpeedClicked, this);
    CreateStyledButton(row_r, 54, 30, 0x64748B, "STOP", 0, OnRightSpeedClicked, this);
    CreateStyledButton(row_r, 54, 30, 0x0D9488, "+10%", 10, OnRightSpeedClicked, this);
    CreateStyledButton(row_r, 54, 30, 0x14B8A6, "+30%", 30, OnRightSpeedClicked, this);

    // Dual Wheel Quick Actions row
    lv_obj_t* row_both = lv_obj_create(panel_servo_);
    lv_obj_set_size(row_both, 312, 32);
    lv_obj_align(row_both, LV_ALIGN_TOP_MID, 0, 138);
    lv_obj_set_style_bg_opa(row_both, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(row_both, 0, 0);
    lv_obj_set_style_pad_all(row_both, 0, 0);
    lv_obj_set_flex_flow(row_both, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row_both, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER);

    CreateStyledButton(row_both, 92, 30, 0x3B82F6, "FWD 20%", 20, OnBothSpeedClicked, this);
    CreateStyledButton(row_both, 92, 30, 0xEF4444, "STOP", 0, OnBothSpeedClicked, this);
    CreateStyledButton(row_both, 92, 30, 0x8B5CF6, "REV 20%", -20, OnBothSpeedClicked, this);

    // Back to Chat button
    CreateStyledButton(panel_servo_, 304, 34, 0x4F46E5, "< BACK TO CHAT", 0,
                       OnBackFromServoClicked, this);
    lv_obj_align(lv_obj_get_child(panel_servo_, lv_obj_get_child_count(panel_servo_) - 1),
                 LV_ALIGN_BOTTOM_MID, 0, -4);
}

void ServoControlUi::CreateGyroPanel() {
    lv_obj_t* screen = lv_screen_active();
    panel_gyro_ = lv_obj_create(screen);
    lv_obj_set_size(panel_gyro_, 320, 240);
    lv_obj_set_pos(panel_gyro_, 0, 0);
    lv_obj_set_style_bg_color(panel_gyro_, lv_color_hex(0x181825), 0);
    lv_obj_set_style_border_width(panel_gyro_, 0, 0);
    lv_obj_set_style_pad_all(panel_gyro_, 8, 0);
    lv_obj_set_scrollbar_mode(panel_gyro_, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(panel_gyro_, LV_OBJ_FLAG_HIDDEN);

    // Title
    lv_obj_t* title = lv_label_create(panel_gyro_);
    lv_label_set_text(title, "QMI8658 6-AXIS IMU & ATTITUDE");
    lv_obj_set_style_text_color(title, lv_color_hex(0x89DCEB), 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 6, 4);

    // Status label
    label_gyro_status_ = lv_label_create(panel_gyro_);
    lv_label_set_text(label_gyro_status_, "STATUS: PROBING...");
    lv_obj_set_style_text_color(label_gyro_status_, lv_color_hex(0xA6E3A1), 0);
    lv_obj_align(label_gyro_status_, LV_ALIGN_TOP_LEFT, 6, 26);

    // Acceleration label
    label_accel_ = lv_label_create(panel_gyro_);
    lv_label_set_text(label_accel_, "ACCEL (g):\n  X: 0.00\n  Y: 0.00\n  Z: 0.00");
    lv_obj_set_style_text_color(label_accel_, lv_color_hex(0xF9E2AF), 0);
    lv_obj_align(label_accel_, LV_ALIGN_TOP_LEFT, 6, 50);

    // Gyroscope label
    label_gyro_ = lv_label_create(panel_gyro_);
    lv_label_set_text(label_gyro_, "GYRO (dps):\n  X: 0.0\n  Y: 0.0\n  Z: 0.0");
    lv_obj_set_style_text_color(label_gyro_, lv_color_hex(0xF38BA8), 0);
    lv_obj_align(label_gyro_, LV_ALIGN_TOP_LEFT, 160, 50);

    // Tilt / Attitude label
    label_tilt_ = lv_label_create(panel_gyro_);
    lv_label_set_text(label_tilt_, "TILT ATTITUDE:\n  Pitch: 0.0 deg\n  Roll:  0.0 deg");
    lv_obj_set_style_text_color(label_tilt_, lv_color_hex(0xCBA6F7), 0);
    lv_obj_align(label_tilt_, LV_ALIGN_TOP_LEFT, 6, 126);

    // Back to Chat button
    CreateStyledButton(panel_gyro_, 304, 34, 0x4F46E5, "< BACK TO CHAT", 0,
                       OnBackFromGyroClicked, this);
    lv_obj_align(lv_obj_get_child(panel_gyro_, lv_obj_get_child_count(panel_gyro_) - 1),
                 LV_ALIGN_BOTTOM_MID, 0, -4);
}

void ServoControlUi::CreateCameraPanel() {
    lv_obj_t* screen = lv_screen_active();
    panel_cam_ = lv_obj_create(screen);
    lv_obj_set_size(panel_cam_, 320, 240);
    lv_obj_set_pos(panel_cam_, 0, 0);
    lv_obj_set_style_bg_color(panel_cam_, lv_color_hex(0x181825), 0);
    lv_obj_set_style_border_width(panel_cam_, 0, 0);
    lv_obj_set_style_pad_all(panel_cam_, 4, 0);
    lv_obj_set_scrollbar_mode(panel_cam_, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(panel_cam_, LV_OBJ_FLAG_HIDDEN);

    // Title
    lv_obj_t* title = lv_label_create(panel_cam_);
    lv_label_set_text(title, "GC2145 CAMERA PREVIEW");
    lv_obj_set_style_text_color(title, lv_color_hex(0x89DCEB), 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 6, 4);

    // Snap Button
    CreateStyledButton(panel_cam_, 96, 24, 0x059669, "SNAP FRAME", 0, OnCameraSnapClicked, this);
    lv_obj_align(lv_obj_get_child(panel_cam_, lv_obj_get_child_count(panel_cam_) - 1),
                 LV_ALIGN_TOP_RIGHT, -6, 2);

    // Cam Info
    label_cam_info_ = lv_label_create(panel_cam_);
    lv_label_set_text(label_cam_info_, "Resolution: 320x240 RGB565");
    lv_obj_set_style_text_color(label_cam_info_, lv_color_hex(0xBAC2DE), 0);
    lv_obj_align(label_cam_info_, LV_ALIGN_TOP_LEFT, 6, 26);

    // Image display object
    img_preview_ = lv_image_create(panel_cam_);
    lv_obj_set_size(img_preview_, 240, 140);
    lv_obj_align(img_preview_, LV_ALIGN_TOP_MID, 0, 50);

    // Back to Chat button
    CreateStyledButton(panel_cam_, 304, 32, 0x4F46E5, "< BACK TO CHAT", 0,
                       OnBackFromCamClicked, this);
    lv_obj_align(lv_obj_get_child(panel_cam_, lv_obj_get_child_count(panel_cam_) - 1),
                 LV_ALIGN_BOTTOM_MID, 0, -4);
}

void ServoControlUi::ShowServoPanel() {
    if (panel_servo_ != nullptr) {
        lv_obj_remove_flag(panel_servo_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(panel_servo_);
        servo_panel_visible_ = true;
        UpdateServoLabels();
        ESP_LOGI(TAG, "Entered servo debugging panel");
    }
}

void ServoControlUi::HideServoPanel() {
    if (panel_servo_ != nullptr) {
        lv_obj_add_flag(panel_servo_, LV_OBJ_FLAG_HIDDEN);
        servo_panel_visible_ = false;
        if (motion_ != nullptr) {
            motion_->Stop();
        }
        UpdateServoLabels();
        ESP_LOGI(TAG, "Exited servo panel, servos stopped");
    }
}

void ServoControlUi::ShowGyroPanel() {
    if (panel_gyro_ != nullptr) {
        lv_obj_remove_flag(panel_gyro_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(panel_gyro_);
        gyro_panel_visible_ = true;
        UpdateGyroLabels();
        ESP_LOGI(TAG, "Entered gyroscope monitor panel");
    }
}

void ServoControlUi::HideGyroPanel() {
    if (panel_gyro_ != nullptr) {
        lv_obj_add_flag(panel_gyro_, LV_OBJ_FLAG_HIDDEN);
        gyro_panel_visible_ = false;
        ESP_LOGI(TAG, "Exited gyroscope monitor panel");
    }
}

void ServoControlUi::ShowCameraPanel() {
    if (panel_cam_ != nullptr) {
        lv_obj_remove_flag(panel_cam_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(panel_cam_);
        cam_panel_visible_ = true;
        UpdateCameraPreview();
        ESP_LOGI(TAG, "Entered camera preview panel");
    }
}

void ServoControlUi::HideCameraPanel() {
    if (panel_cam_ != nullptr) {
        lv_obj_add_flag(panel_cam_, LV_OBJ_FLAG_HIDDEN);
        cam_panel_visible_ = false;
        ESP_LOGI(TAG, "Exited camera preview panel");
    }
}

void ServoControlUi::UpdateServoLabels() {
    if (motion_ == nullptr) return;
    int l_spd = motion_->GetLeftSpeed();
    int r_spd = motion_->GetRightSpeed();
    uint16_t l_us = motion_->GetLeftPulseUs();
    uint16_t r_us = motion_->GetRightPulseUs();

    char buf[64];
    if (label_left_ != nullptr) {
        snprintf(buf, sizeof(buf), "L (CH0): %d%% [%uus]", l_spd, l_us);
        lv_label_set_text(label_left_, buf);
    }
    if (label_right_ != nullptr) {
        snprintf(buf, sizeof(buf), "R (CH1): %d%% [%uus]", r_spd, r_us);
        lv_label_set_text(label_right_, buf);
    }
}

void ServoControlUi::UpdateGyroLabels() {
    if (imu_ == nullptr || !imu_->IsInitialized()) {
        if (label_gyro_status_ != nullptr) {
            lv_label_set_text(label_gyro_status_, "STATUS: SENSOR NOT FOUND / PROBING");
            lv_obj_set_style_text_color(label_gyro_status_, lv_color_hex(0xEF4444), 0);
        }
        return;
    }

    if (label_gyro_status_ != nullptr) {
        lv_label_set_text(label_gyro_status_, "STATUS: CONNECTED (0x6A)");
        lv_obj_set_style_text_color(label_gyro_status_, lv_color_hex(0xA6E3A1), 0);
    }

    ImuData d;
    if (imu_->ReadData(d) == ESP_OK) {
        char buf[128];
        if (label_accel_ != nullptr) {
            snprintf(buf, sizeof(buf), "ACCEL (g):\n  X: %+.2f\n  Y: %+.2f\n  Z: %+.2f", d.ax, d.ay, d.az);
            lv_label_set_text(label_accel_, buf);
        }
        if (label_gyro_ != nullptr) {
            snprintf(buf, sizeof(buf), "GYRO (dps):\n  X: %+.1f\n  Y: %+.1f\n  Z: %+.1f", d.gx, d.gy, d.gz);
            lv_label_set_text(label_gyro_, buf);
        }
        if (label_tilt_ != nullptr) {
            snprintf(buf, sizeof(buf), "TILT ATTITUDE:\n  Pitch: %+.1f deg\n  Roll:  %+.1f deg", d.pitch, d.roll);
            lv_label_set_text(label_tilt_, buf);
        }
    }
}

void ServoControlUi::UpdateCameraPreview() {
    if (camera_ == nullptr) {
        if (label_cam_info_ != nullptr) {
            lv_label_set_text(label_cam_info_, "Camera object not available");
        }
        return;
    }

    if (!camera_->Capture()) {
        if (label_cam_info_ != nullptr) {
            lv_label_set_text(label_cam_info_, "Camera capture failed");
        }
        return;
    }

    camera_fb_t* fb = camera_->GetCurrentFrameBuffer();
    if (fb == nullptr || fb->buf == nullptr) {
        if (label_cam_info_ != nullptr) {
            lv_label_set_text(label_cam_info_, "Empty frame buffer");
        }
        return;
    }

    s_cam_dsc.header.cf = LV_COLOR_FORMAT_RGB565;
    s_cam_dsc.header.w = fb->width;
    s_cam_dsc.header.h = fb->height;
    s_cam_dsc.data_size = fb->len;
    s_cam_dsc.data = fb->buf;

    if (img_preview_ != nullptr) {
        lv_image_set_src(img_preview_, &s_cam_dsc);
    }
    if (label_cam_info_ != nullptr) {
        char buf[64];
        snprintf(buf, sizeof(buf), "Live Frame: %dx%d (%zu B)", fb->width, fb->height, fb->len);
        lv_label_set_text(label_cam_info_, buf);
    }
}

void ServoControlUi::OnPeriodicTimer(lv_timer_t* t) {
    auto* ui = static_cast<ServoControlUi*>(lv_timer_get_user_data(t));
    if (ui == nullptr) return;

    if (ui->servo_panel_visible_) {
        ui->UpdateServoLabels();
    } else if (ui->gyro_panel_visible_) {
        ui->UpdateGyroLabels();
    }
}

void ServoControlUi::OnEntranceServoClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) ui->ShowServoPanel();
}

void ServoControlUi::OnEntranceGyroClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) ui->ShowGyroPanel();
}

void ServoControlUi::OnEntranceCamClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) ui->ShowCameraPanel();
}

void ServoControlUi::OnBackFromServoClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) ui->HideServoPanel();
}

void ServoControlUi::OnBackFromGyroClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) ui->HideGyroPanel();
}

void ServoControlUi::OnBackFromCamClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) ui->HideCameraPanel();
}

void ServoControlUi::OnStopAllClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->motion_ != nullptr) {
        ui->motion_->Stop();
        ui->UpdateServoLabels();
        ESP_LOGI(TAG, "STOP ALL clicked: servos stopped at 1560us neutral");
    }
}

void ServoControlUi::OnLeftSpeedClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->motion_ != nullptr) {
        lv_obj_t* btn = static_cast<lv_obj_t*>(lv_event_get_target(e));
        int speed = (int)(intptr_t)lv_obj_get_user_data(btn);
        ui->motion_->SetLeftSpeed(speed);
        ui->UpdateServoLabels();
    }
}

void ServoControlUi::OnRightSpeedClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->motion_ != nullptr) {
        lv_obj_t* btn = static_cast<lv_obj_t*>(lv_event_get_target(e));
        int speed = (int)(intptr_t)lv_obj_get_user_data(btn);
        ui->motion_->SetRightSpeed(speed);
        ui->UpdateServoLabels();
    }
}

void ServoControlUi::OnBothSpeedClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->motion_ != nullptr) {
        lv_obj_t* btn = static_cast<lv_obj_t*>(lv_event_get_target(e));
        int speed = (int)(intptr_t)lv_obj_get_user_data(btn);
        if (speed == 0) {
            ui->motion_->Stop();
        } else {
            ui->motion_->DriveForDuration(speed, speed, 2000);
        }
        ui->UpdateServoLabels();
    }
}

void ServoControlUi::OnCameraSnapClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) {
        ui->UpdateCameraPreview();
    }
}
