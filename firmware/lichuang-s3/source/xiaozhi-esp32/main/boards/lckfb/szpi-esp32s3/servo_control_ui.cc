#include "servo_control_ui.h"
#include <esp_log.h>
#include <cstdio>

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
}  // namespace

ServoControlUi::ServoControlUi(DualWheelController* controller)
    : controller_(controller) {}

ServoControlUi::~ServoControlUi() {
    if (panel_ != nullptr) {
        lv_obj_delete(panel_);
        panel_ = nullptr;
    }
    if (entrance_btn_ != nullptr) {
        lv_obj_delete(entrance_btn_);
        entrance_btn_ = nullptr;
    }
}

void ServoControlUi::SetupUI() {
    CreateEntranceButton();
    CreatePanel();
    UpdateStatusLabels();
    ESP_LOGI(TAG, "Servo touch UI initialized successfully");
}

void ServoControlUi::ShowPanel() {
    if (panel_ != nullptr) {
        lv_obj_remove_flag(panel_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(panel_);
        panel_visible_ = true;
        UpdateStatusLabels();
        ESP_LOGI(TAG, "Entered servo debugging panel");
    }
}

void ServoControlUi::HidePanel() {
    if (panel_ != nullptr) {
        lv_obj_add_flag(panel_, LV_OBJ_FLAG_HIDDEN);
        panel_visible_ = false;
        if (controller_ != nullptr) {
            controller_->Stop();
        }
        UpdateStatusLabels();
        ESP_LOGI(TAG, "Returned to chat screen, servos stopped");
    }
}

void ServoControlUi::CreateEntranceButton() {
    lv_obj_t* screen = lv_screen_active();
    entrance_btn_ = lv_button_create(screen);
    lv_obj_set_size(entrance_btn_, 72, 30);
    lv_obj_align(entrance_btn_, LV_ALIGN_TOP_LEFT, 6, 26);
    lv_obj_set_style_bg_color(entrance_btn_, lv_color_hex(0x2563EB), 0);  // Bright blue
    lv_obj_set_style_radius(entrance_btn_, 6, 0);

    lv_obj_t* lbl = lv_label_create(entrance_btn_);
    lv_label_set_text(lbl, "SERVO");
    lv_obj_center(lbl);

    lv_obj_add_event_cb(entrance_btn_, OnEntranceClicked, LV_EVENT_CLICKED, this);
}

void ServoControlUi::CreatePanel() {
    lv_obj_t* screen = lv_screen_active();
    panel_ = lv_obj_create(screen);
    lv_obj_set_size(panel_, 320, 240);
    lv_obj_set_pos(panel_, 0, 0);
    lv_obj_set_style_bg_color(panel_, lv_color_hex(0x181825), 0);  // Dark background
    lv_obj_set_style_border_width(panel_, 0, 0);
    lv_obj_set_style_pad_all(panel_, 4, 0);
    lv_obj_set_scrollbar_mode(panel_, LV_SCROLLBAR_MODE_OFF);
    lv_obj_add_flag(panel_, LV_OBJ_FLAG_HIDDEN);

    // Title label
    lv_obj_t* title = lv_label_create(panel_);
    lv_label_set_text(title, "DESKBOT SERVO CONTROL");
    lv_obj_set_style_text_color(title, lv_color_hex(0x89DCEB), 0);
    lv_obj_align(title, LV_ALIGN_TOP_LEFT, 8, 4);

    // Header STOP ALL button
    CreateStyledButton(panel_, 84, 22, 0xEF4444, "STOP ALL", 0, OnStopAllClicked, this);
    lv_obj_align(lv_obj_get_child(panel_, lv_obj_get_child_count(panel_) - 1),
                 LV_ALIGN_TOP_RIGHT, -6, 2);

    // Left Servo (CH0) label (y = 28)
    label_left_ = lv_label_create(panel_);
    lv_obj_set_style_text_color(label_left_, lv_color_hex(0xF9E2AF), 0);
    lv_obj_align(label_left_, LV_ALIGN_TOP_LEFT, 8, 28);

    // Left Servo buttons row (y = 48)
    lv_obj_t* row_l = lv_obj_create(panel_);
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

    // Right Servo (CH1) label (y = 82)
    label_right_ = lv_label_create(panel_);
    lv_obj_set_style_text_color(label_right_, lv_color_hex(0xA6E3A1), 0);
    lv_obj_align(label_right_, LV_ALIGN_TOP_LEFT, 8, 82);

    // Right Servo buttons row (y = 102)
    lv_obj_t* row_r = lv_obj_create(panel_);
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

    // Dual Wheel Quick Actions row (y = 138)
    lv_obj_t* row_both = lv_obj_create(panel_);
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

    // Back to Chat button (y = 176)
    CreateStyledButton(panel_, 304, 34, 0x4F46E5, "< BACK TO CHAT", 0,
                       OnBackClicked, this);
    lv_obj_align(lv_obj_get_child(panel_, lv_obj_get_child_count(panel_) - 1),
                 LV_ALIGN_BOTTOM_MID, 0, -4);
}

void ServoControlUi::UpdateStatusLabels() {
    if (controller_ == nullptr) return;
    int l_spd = controller_->GetLeftSpeed();
    int r_spd = controller_->GetRightSpeed();
    uint16_t l_us = controller_->GetLeftPulseUs();
    uint16_t r_us = controller_->GetRightPulseUs();

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

void ServoControlUi::OnEntranceClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) {
        ui->ShowPanel();
    }
}

void ServoControlUi::OnBackClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr) {
        ui->HidePanel();
    }
}

void ServoControlUi::OnStopAllClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->controller_ != nullptr) {
        ui->controller_->Stop();
        ui->UpdateStatusLabels();
        ESP_LOGI(TAG, "STOP ALL clicked: servos stopped at 1560us neutral");
    }
}

void ServoControlUi::OnLeftSpeedClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->controller_ != nullptr) {
        lv_obj_t* btn = static_cast<lv_obj_t*>(lv_event_get_target(e));
        int speed = (int)(intptr_t)lv_obj_get_user_data(btn);
        ui->controller_->SetLeftSpeed(speed);
        ui->UpdateStatusLabels();
        ESP_LOGI(TAG, "Left servo set to %d%% (pulse: %uus)", speed, ui->controller_->GetLeftPulseUs());
    }
}

void ServoControlUi::OnRightSpeedClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->controller_ != nullptr) {
        lv_obj_t* btn = static_cast<lv_obj_t*>(lv_event_get_target(e));
        int speed = (int)(intptr_t)lv_obj_get_user_data(btn);
        ui->controller_->SetRightSpeed(speed);
        ui->UpdateStatusLabels();
        ESP_LOGI(TAG, "Right servo set to %d%% (pulse: %uus)", speed, ui->controller_->GetRightPulseUs());
    }
}

void ServoControlUi::OnBothSpeedClicked(lv_event_t* e) {
    auto* ui = static_cast<ServoControlUi*>(lv_event_get_user_data(e));
    if (ui != nullptr && ui->controller_ != nullptr) {
        lv_obj_t* btn = static_cast<lv_obj_t*>(lv_event_get_target(e));
        int speed = (int)(intptr_t)lv_obj_get_user_data(btn);
        if (speed == 0) {
            ui->controller_->Stop();
        } else {
            // Safety: run for 2000ms then auto-stop
            ui->controller_->SetWheelSpeedsForDuration(speed, speed, 2000);
        }
        ui->UpdateStatusLabels();
        ESP_LOGI(TAG, "Both servos set to %d%% (safety duration 2000ms)", speed);
    }
}
