#include "LCD_Driver.h"
#include "LVGL_Driver.h"
#include "LVGL_Example.h"
#include "Eye_UART.h"

void setup()
{
  LCD_INIT();
  Lvgl_Init();
  Lvgl_Example1();
  EyeUart_Init();

  vTaskDelay(pdMS_TO_TICKS(100));
  LVGL_Start();
}

void loop() {
  EyeUart_Update();
  vTaskDelay(pdMS_TO_TICKS(5));
}
