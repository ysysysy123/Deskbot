#include "servo_control_ui.h"

#include <cstdio>
#include <cstring>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_lvgl_port.h>

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
    lv_obj_set_style_text_font(lbl, &lv_font_montserrat_14, 0);
    lv_obj_center(lbl);

    lv_obj_add_event_cb(btn, cb, LV_EVENT_CLICKED, user_data);
    return btn;
}

lv_obj_t* CreateInfoCard(lv_obj_t* parent, int x, int y, int w, int h, uint32_t border_hex,
                         const char* title, uint32_t title_hex) {
    lv_obj_t* card = lv_obj_create(parent);
    lv_obj_set_pos(card, x, y);
    lv_obj_set_size(card, w, h);
    lv_obj_set_style_bg_color(card, lv_color_hex(0x24273A), 0);
    lv_obj_set_style_border_color(card, lv_color_hex(border_hex), 0);
    lv_obj_set_style_border_width(card, 1, 0);
    lv_obj_set_style_radius(card, 8, 0);
    lv_obj_set_style_pad_all(card, 4, 0);
    lv_obj_set_scrollbar_mode(card, LV_SCROLLBAR_MODE_OFF);

    lv_obj_t* t = lv_label_create(card);
    lv_label_set_text(t, title);
    lv_obj_set_style_text_font(t, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(t, lv_color_hex(title_hex), 0);
    lv_obj_align(t, LV_ALIGN_TOP_MID, 0, 0);
    return card;
}
}  // namespace

ServoControlUi::ServoControlUi(MotionController* motion, Qmi8658* imu, Esp32Camera* camera)
    : motion_(motion), imu_(imu), camera_(camera) {
    cam_preview_buf_ = (uint16_t*)heap_caps_malloc(320 * 240 * sizeof(uint16_t),
                                                   MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (cam_preview_buf_ == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate 320x240 preview buffer in PSRAM");
    }
    memset(&cam_img_dsc_, 0, sizeof(cam_img_dsc_));
}

ServoControlUi::~ServoControlUi() {
    lvgl_port_lock(0);
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
    lvgl_port_unlock();

    if (cam_preview_buf_ != nullptr) {
        heap_caps_free(cam_preview_buf_);
        cam_preview_buf_ = nullptr;
    }
}

void ServoControlUi::SetupUI() {
    CreateEntranceButtons();
    CreateServoPanel();
    CreateGyroPanel();
    CreateCameraPanel();

    refresh_timer_ = lv_timer_create(OnPeriodicTimer, 100, this);
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
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0x89DCEB), 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 8, 4);

    // Header STOP ALL button
    CreateStyledButton(panel_servo_, 84, 22, 0xEF4444, "STOP ALL", 0, OnStopAllClicked, this);
    lv_obj_align(lv_obj_get_child(panel_servo_, lv_obj_get_child_count(panel_servo_) - 1),
                 LV_ALIGN_TOP_RIGHT, -6, 2);

    // Left Servo (CH0) label
    label_left_ = lv_label_create(panel_servo_);
    lv_obj_set_style_text_font(label_left_, &lv_font_montserrat_14, 0);
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
    lv_obj_set_style_text_font(label_right_, &lv_font_montserrat_14, 0);
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
    lv_obj_set_style_pad_all(panel_gyro_, 4, 0);
    lv_obj_set_scrollbar_mode(panel_gyro_, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(panel_gyro_, LV_OBJ_FLAG_HIDDEN);

    // Title
    lv_obj_t* title = lv_label_create(panel_gyro_);
    lv_label_set_text(title, "IMU (QMI8658)");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0x89DCEB), 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 8, 4);

    // Status label
    label_gyro_status_ = lv_label_create(panel_gyro_);
    lv_label_set_text(label_gyro_status_, "[ONLINE 0x6A]");
    lv_obj_set_style_text_font(label_gyro_status_, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(label_gyro_status_, lv_color_hex(0xA6E3A1), 0);
    lv_obj_align(label_gyro_status_, LV_ALIGN_TOP_RIGHT, -8, 4);

    // 3 Cards: Accel, Gyro, Attitude (height 164, y=24)
    // Card 1: Accel (x=6, y=24, w=98, h=164)
    lv_obj_t* card_a = CreateInfoCard(panel_gyro_, 6, 24, 98, 164, 0xF9E2AF, "ACCEL (g)", 0xF9E2AF);
    label_accel_ = lv_label_create(card_a);
    lv_label_set_text(label_accel_, "X: +0.00\nY: +0.00\nZ: +0.00");
    lv_obj_set_style_text_font(label_accel_, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(label_accel_, lv_color_hex(0xF9E2AF), 0);
    lv_obj_align(label_accel_, LV_ALIGN_TOP_LEFT, 4, 22);

    // Card 2: Gyro (x=110, y=24, w=98, h=164)
    lv_obj_t* card_g = CreateInfoCard(panel_gyro_, 110, 24, 98, 164, 0xF38BA8, "GYRO (dps)", 0xF38BA8);
    label_gyro_ = lv_label_create(card_g);
    lv_label_set_text(label_gyro_, "X: +0.0\nY: +0.0\nZ: +0.0");
    lv_obj_set_style_text_font(label_gyro_, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(label_gyro_, lv_color_hex(0xF38BA8), 0);
    lv_obj_align(label_gyro_, LV_ALIGN_TOP_LEFT, 4, 22);

    // Card 3: Attitude (x=214, y=24, w=100, h=164)
    lv_obj_t* card_t = CreateInfoCard(panel_gyro_, 214, 24, 100, 164, 0xCBA6F7, "ATTITUDE", 0xCBA6F7);
    label_tilt_ = lv_label_create(card_t);
    lv_label_set_text(label_tilt_, "Pitch:\n+0.0 deg\n\nRoll:\n+0.0 deg");
    lv_obj_set_style_text_font(label_tilt_, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(label_tilt_, lv_color_hex(0xCBA6F7), 0);
    lv_obj_align(label_tilt_, LV_ALIGN_TOP_LEFT, 4, 22);

    // Back to Chat button
    CreateStyledButton(panel_gyro_, 308, 36, 0x4F46E5, "< BACK TO CHAT", 0,
                       OnBackFromGyroClicked, this);
    lv_obj_align(lv_obj_get_child(panel_gyro_, lv_obj_get_child_count(panel_gyro_) - 1),
                 LV_ALIGN_BOTTOM_MID, 0, -4);
}

void ServoControlUi::CreateCameraPanel() {
    lv_obj_t* screen = lv_screen_active();
    panel_cam_ = lv_obj_create(screen);
    lv_obj_set_size(panel_cam_, 320, 240);
    lv_obj_set_pos(panel_cam_, 0, 0);
    lv_obj_set_style_bg_color(panel_cam_, lv_color_hex(0x000000), 0);
    lv_obj_set_style_border_width(panel_cam_, 0, 0);
    lv_obj_set_style_pad_all(panel_cam_, 0, 0);
    lv_obj_set_scrollbar_mode(panel_cam_, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(panel_cam_, LV_OBJ_FLAG_HIDDEN);

    // Image preview object (320x240 full screen)
    img_preview_ = lv_image_create(panel_cam_);
    lv_obj_set_size(img_preview_, 320, 240);
    lv_obj_set_pos(img_preview_, 0, 0);

    // Semi-transparent top bar
    lv_obj_t* top_bar = lv_obj_create(panel_cam_);
    lv_obj_set_size(top_bar, 320, 28);
    lv_obj_set_pos(top_bar, 0, 0);
    lv_obj_set_style_bg_color(top_bar, lv_color_hex(0x181825), 0);
    lv_obj_set_style_bg_opa(top_bar, LV_OPA_70, 0);
    lv_obj_set_style_border_width(top_bar, 0, 0);
    lv_obj_set_style_pad_all(top_bar, 2, 0);
    lv_obj_set_scrollbar_mode(top_bar, LV_SCROLLBAR_MODE_OFF);

    // Title
    lv_obj_t* title = lv_label_create(top_bar);
    lv_label_set_text(title, "CAMERA LIVE PREVIEW");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0x89DCEB), 0);
    lv_obj_align(title, LV_ALIGN_LEFT_MID, 8, 0);

    // Cam Info badge
    label_cam_info_ = lv_label_create(top_bar);
    lv_label_set_text(label_cam_info_, "320x240 LIVE");
    lv_obj_set_style_text_font(label_cam_info_, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(label_cam_info_, lv_color_hex(0x10B981), 0);
    lv_obj_align(label_cam_info_, LV_ALIGN_RIGHT_MID, -8, 0);

    // Semi-transparent floating Back to Chat button at bottom
    lv_obj_t* btn_back = CreateStyledButton(panel_cam_, 160, 32, 0x4F46E5, "< BACK TO CHAT", 0,
                                            OnBackFromCamClicked, this);
    lv_obj_align(btn_back, LV_ALIGN_BOTTOM_MID, 0, -8);
    lv_obj_set_style_bg_opa(btn_back, LV_OPA_80, 0);
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
            lv_label_set_text(label_gyro_status_, "DISCONNECTED");
            lv_obj_set_style_text_color(label_gyro_status_, lv_color_hex(0xEF4444), 0);
        }
        return;
    }

    if (label_gyro_status_ != nullptr) {
        lv_label_set_text(label_gyro_status_, "ONLINE (0x6A)");
        lv_obj_set_style_text_color(label_gyro_status_, lv_color_hex(0xA6E3A1), 0);
    }

    ImuData d;
    if (imu_->ReadData(d) == ESP_OK) {
        char buf[64];
        if (label_accel_ != nullptr) {
            snprintf(buf, sizeof(buf), "X: %+.2f\nY: %+.2f\nZ: %+.2f", d.ax, d.ay, d.az);
            lv_label_set_text(label_accel_, buf);
        }
        if (label_gyro_ != nullptr) {
            snprintf(buf, sizeof(buf), "X: %+.1f\nY: %+.1f\nZ: %+.1f", d.gx, d.gy, d.gz);
            lv_label_set_text(label_gyro_, buf);
        }
        if (label_tilt_ != nullptr) {
            snprintf(buf, sizeof(buf), "Pitch:\n%+.1f deg\n\nRoll:\n%+.1f deg", d.pitch, d.roll);
            lv_label_set_text(label_tilt_, buf);
        }
    }
}

void ServoControlUi::UpdateCameraPreview() {
    if (camera_ == nullptr || cam_preview_buf_ == nullptr) {
        if (label_cam_info_ != nullptr) {
            lv_label_set_text(label_cam_info_, "NO CAMERA");
            lv_obj_set_style_text_color(label_cam_info_, lv_color_hex(0xEF4444), 0);
        }
        return;
    }

    if (!camera_->Capture()) {
        if (label_cam_info_ != nullptr) {
            lv_label_set_text(label_cam_info_, "CAPTURE FAILED");
            lv_obj_set_style_text_color(label_cam_info_, lv_color_hex(0xEF4444), 0);
        }
        return;
    }

    camera_fb_t* fb = camera_->GetCurrentFrameBuffer();
    if (fb == nullptr || fb->buf == nullptr) {
        if (label_cam_info_ != nullptr) {
            lv_label_set_text(label_cam_info_, "EMPTY BUFFER");
            lv_obj_set_style_text_color(label_cam_info_, lv_color_hex(0xEF4444), 0);
        }
        return;
    }

    // Direct 1:1 pixel mapping: 320x240 native QVGA.
    // Convert big-endian RGB565 from DVP to little-endian RGB565 for LVGL.
    const uint16_t* src = reinterpret_cast<const uint16_t*>(fb->buf);
    size_t pixel_count = 320 * 240;
    for (size_t i = 0; i < pixel_count; ++i) {
        cam_preview_buf_[i] = __builtin_bswap16(src[i]);
    }

    cam_img_dsc_.header.magic = LV_IMAGE_HEADER_MAGIC;
    cam_img_dsc_.header.cf = LV_COLOR_FORMAT_RGB565;
    cam_img_dsc_.header.w = 320;
    cam_img_dsc_.header.h = 240;
    cam_img_dsc_.header.stride = 320 * sizeof(uint16_t);
    cam_img_dsc_.header.flags = 0;
    cam_img_dsc_.data_size = 320 * 240 * sizeof(uint16_t);
    cam_img_dsc_.data = reinterpret_cast<const uint8_t*>(cam_preview_buf_);

    if (img_preview_ != nullptr) {
        lv_image_set_src(img_preview_, &cam_img_dsc_);
        lv_obj_invalidate(img_preview_);
    }
    if (label_cam_info_ != nullptr) {
        lv_label_set_text(label_cam_info_, "320x240 LIVE");
        lv_obj_set_style_text_color(label_cam_info_, lv_color_hex(0x10B981), 0);
    }
}

void ServoControlUi::OnPeriodicTimer(lv_timer_t* t) {
    auto* ui = static_cast<ServoControlUi*>(lv_timer_get_user_data(t));
    if (ui == nullptr) return;

    if (ui->servo_panel_visible_) {
        ui->UpdateServoLabels();
    } else if (ui->gyro_panel_visible_) {
        ui->UpdateGyroLabels();
    } else if (ui->cam_panel_visible_) {
        ui->UpdateCameraPreview();
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
