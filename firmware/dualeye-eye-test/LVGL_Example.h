#pragma once

#include <stdint.h>

#include "LVGL_Driver.h"
#include "LCD_Driver.h"

enum class EyeState : uint8_t {
  IDLE,
  LISTENING,
  THINKING,
  SPEAKING,
  HAPPY,
  SAD,
  ANGRY,
  SURPRISED,
  SLEEPING,
};

void Lvgl_Example1(void);
bool Eye_RequestState(EyeState state);
bool Eye_RequestGaze(int8_t x_percent, int8_t y_percent);
bool Eye_RequestBlink(void);
void LVGL_Backlight_adjustment(uint8_t Backlight);
