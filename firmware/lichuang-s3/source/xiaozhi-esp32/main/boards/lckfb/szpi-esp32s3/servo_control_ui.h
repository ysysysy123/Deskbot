#ifndef SERVO_CONTROL_UI_H
#define SERVO_CONTROL_UI_H

#include <lvgl.h>
#include "dual_wheel_controller.h"

class ServoControlUi {
public:
    explicit ServoControlUi(DualWheelController* controller);
    ~ServoControlUi();

    void SetupUI();
    void ShowPanel();
    void HidePanel();
    bool IsPanelVisible() const { return panel_visible_; }

private:
    void CreateEntranceButton();
    void CreatePanel();
    void UpdateStatusLabels();

    static void OnEntranceClicked(lv_event_t* e);
    static void OnBackClicked(lv_event_t* e);
    static void OnLeftSpeedClicked(lv_event_t* e);
    static void OnRightSpeedClicked(lv_event_t* e);
    static void OnBothSpeedClicked(lv_event_t* e);
    static void OnStopAllClicked(lv_event_t* e);

    DualWheelController* controller_ = nullptr;
    lv_obj_t* entrance_btn_ = nullptr;
    lv_obj_t* panel_ = nullptr;
    lv_obj_t* label_left_ = nullptr;
    lv_obj_t* label_right_ = nullptr;
    bool panel_visible_ = false;
};

#endif  // SERVO_CONTROL_UI_H
