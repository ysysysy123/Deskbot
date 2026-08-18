#include "LVGL_Example.h"

#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

namespace {

constexpr int16_t EYE_WIDTH = 176;
constexpr int16_t EYE_HEIGHT = 164;
constexpr int16_t IRIS_SIZE = 74;
constexpr int16_t PUPIL_SIZE = 40;
constexpr int16_t BLINK_HEIGHT = 8;

enum class EyeCommandType : uint8_t { STATE, GAZE, BLINK };

struct EyeCommand {
  EyeCommandType type;
  EyeState state;
  int8_t x;
  int8_t y;
};

struct EyeView {
  lv_obj_t *eyeball;
  lv_obj_t *iris;
};

EyeView left_eye = {};
EyeView right_eye = {};
lv_timer_t *controller_timer = NULL;
QueueHandle_t command_queue = NULL;
EyeState current_state = EyeState::IDLE;
int32_t gaze_x = 0;
int32_t gaze_y = -4;
int32_t current_eye_height = EYE_HEIGHT;
int32_t base_eye_height = EYE_HEIGHT;
uint32_t next_gaze_at = 0;
uint32_t next_blink_at = 0;
uint32_t manual_gaze_until = 0;
bool thinking_looks_right = false;
bool speaking_moves_down = false;

bool time_reached(uint32_t now, uint32_t deadline)
{
  return static_cast<int32_t>(now - deadline) >= 0;
}

uint32_t random_between(uint32_t minimum, uint32_t maximum)
{
  return minimum + esp_random() % (maximum - minimum);
}

void align_iris(EyeView &eye)
{
  if (eye.iris != NULL) {
    lv_obj_align(eye.iris, LV_ALIGN_CENTER, gaze_x, gaze_y);
  }
}

void update_gaze()
{
  align_iris(left_eye);
  align_iris(right_eye);
}

void gaze_x_anim_cb(void *variable, int32_t value)
{
  (void)variable;
  gaze_x = value;
  update_gaze();
}

void gaze_y_anim_cb(void *variable, int32_t value)
{
  (void)variable;
  gaze_y = value;
  update_gaze();
}

void apply_eye_height(EyeView &eye, int32_t height)
{
  if (eye.eyeball == NULL) {
    return;
  }

  lv_obj_set_height(eye.eyeball, height);
  lv_obj_center(eye.eyeball);
  align_iris(eye);
}

void eye_height_anim_cb(void *variable, int32_t height)
{
  (void)variable;
  current_eye_height = height;
  apply_eye_height(left_eye, height);
  apply_eye_height(right_eye, height);
}

void start_value_animation(int32_t *variable, int32_t target,
                           lv_anim_exec_xcb_t callback, uint32_t duration_ms)
{
  lv_anim_del(variable, callback);

  lv_anim_t animation;
  lv_anim_init(&animation);
  lv_anim_set_var(&animation, variable);
  lv_anim_set_values(&animation, *variable, target);
  lv_anim_set_time(&animation, duration_ms);
  lv_anim_set_path_cb(&animation, lv_anim_path_ease_in_out);
  lv_anim_set_exec_cb(&animation, callback);
  lv_anim_start(&animation);
}

void start_gaze(int32_t x, int32_t y, uint32_t duration_ms)
{
  start_value_animation(&gaze_x, x, gaze_x_anim_cb, duration_ms);
  start_value_animation(&gaze_y, y, gaze_y_anim_cb, duration_ms);
}

void start_random_gaze(uint32_t now)
{
  static const int8_t targets[][2] = {
    {  0, -6}, {-28, -7}, {28, -7}, {-18, 8}, {18, 8}, {0, 10},
  };

  const size_t target_index = esp_random() % (sizeof(targets) / sizeof(targets[0]));
  start_gaze(targets[target_index][0], targets[target_index][1],
             random_between(280, 560));
  next_gaze_at = now + random_between(900, 2600);
}

void start_blink(uint32_t now)
{
  if (current_state == EyeState::SLEEPING) {
    return;
  }

  lv_anim_del(&current_eye_height, eye_height_anim_cb);

  lv_anim_t animation;
  lv_anim_init(&animation);
  lv_anim_set_var(&animation, &current_eye_height);
  lv_anim_set_values(&animation, base_eye_height, BLINK_HEIGHT);
  lv_anim_set_time(&animation, 90);
  lv_anim_set_playback_delay(&animation, 35);
  lv_anim_set_playback_time(&animation, 120);
  lv_anim_set_path_cb(&animation, lv_anim_path_ease_in_out);
  lv_anim_set_exec_cb(&animation, eye_height_anim_cb);
  lv_anim_start(&animation);

  next_blink_at = now + random_between(2800, 6500);
}

void set_iris_appearance(EyeView &eye, int16_t size, lv_color_t color)
{
  if (eye.iris == NULL) {
    return;
  }
  lv_obj_set_size(eye.iris, size, size);
  lv_obj_set_style_bg_color(eye.iris, color, 0);
  align_iris(eye);
}

void apply_state(EyeState state, uint32_t now)
{
  current_state = state;
  manual_gaze_until = 0;
  thinking_looks_right = false;
  speaking_moves_down = false;

  int16_t iris_size = IRIS_SIZE;
  lv_color_t iris_color = lv_color_hex(0x39A8FF);
  int32_t target_x = 0;
  int32_t target_y = -4;
  base_eye_height = EYE_HEIGHT;

  switch (state) {
    case EyeState::LISTENING:
      iris_size = 86;
      iris_color = lv_color_hex(0x26D9C7);
      target_y = -3;
      break;
    case EyeState::THINKING:
      iris_size = 66;
      iris_color = lv_color_hex(0xA66CFF);
      base_eye_height = 150;
      target_x = -24;
      target_y = -5;
      break;
    case EyeState::SPEAKING:
      iris_size = 78;
      iris_color = lv_color_hex(0x39D5FF);
      target_y = -7;
      break;
    case EyeState::HAPPY:
      iris_size = 72;
      iris_color = lv_color_hex(0xFFC84A);
      base_eye_height = 94;
      target_y = 6;
      break;
    case EyeState::SLEEPING:
      base_eye_height = BLINK_HEIGHT;
      target_y = 0;
      break;
    case EyeState::IDLE:
    default:
      break;
  }

  set_iris_appearance(left_eye, iris_size, iris_color);
  set_iris_appearance(right_eye, iris_size, iris_color);
  start_gaze(target_x, target_y, 220);
  start_value_animation(&current_eye_height, base_eye_height,
                        eye_height_anim_cb, 220);

  next_gaze_at = now + ((state == EyeState::THINKING) ? 520 : 900);
  next_blink_at = now + random_between(2200, 4200);
}

void apply_manual_gaze(int8_t x_percent, int8_t y_percent, uint32_t now)
{
  start_gaze(static_cast<int32_t>(x_percent) * 30 / 100,
             static_cast<int32_t>(y_percent) * 14 / 100, 180);
  manual_gaze_until = now + 2000;
}

void process_commands(uint32_t now)
{
  if (command_queue == NULL) {
    return;
  }

  EyeCommand command;
  while (xQueueReceive(command_queue, &command, 0) == pdTRUE) {
    switch (command.type) {
      case EyeCommandType::STATE:
        apply_state(command.state, now);
        break;
      case EyeCommandType::GAZE:
        apply_manual_gaze(command.x, command.y, now);
        break;
      case EyeCommandType::BLINK:
        start_blink(now);
        break;
    }
  }
}

void update_automatic_motion(uint32_t now)
{
  if (manual_gaze_until != 0 && !time_reached(now, manual_gaze_until)) {
    if (time_reached(now, next_blink_at)) {
      start_blink(now);
    }
    return;
  }
  manual_gaze_until = 0;

  switch (current_state) {
    case EyeState::IDLE:
      if (time_reached(now, next_gaze_at)) {
        start_random_gaze(now);
      }
      break;
    case EyeState::LISTENING:
      if (time_reached(now, next_gaze_at)) {
        start_gaze(0, -3, 180);
        next_gaze_at = now + 1200;
      }
      break;
    case EyeState::THINKING:
      if (time_reached(now, next_gaze_at)) {
        thinking_looks_right = !thinking_looks_right;
        start_gaze(thinking_looks_right ? 24 : -24, -5, 260);
        next_gaze_at = now + 520;
      }
      break;
    case EyeState::SPEAKING:
      if (time_reached(now, next_gaze_at)) {
        speaking_moves_down = !speaking_moves_down;
        start_gaze(0, speaking_moves_down ? 4 : -7, 180);
        next_gaze_at = now + 300;
      }
      break;
    case EyeState::HAPPY:
      if (time_reached(now, next_gaze_at)) {
        start_gaze(0, 6, 180);
        next_gaze_at = now + 1500;
      }
      break;
    case EyeState::SLEEPING:
      return;
  }

  if (time_reached(now, next_blink_at)) {
    start_blink(now);
  }
}

void controller_timer_cb(lv_timer_t *timer)
{
  (void)timer;
  const uint32_t now = lv_tick_get();
  process_commands(now);
  update_automatic_motion(now);
}

void create_circle(lv_obj_t *object, int16_t size, lv_color_t color)
{
  lv_obj_remove_style_all(object);
  lv_obj_clear_flag(object, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_size(object, size, size);
  lv_obj_set_style_radius(object, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_color(object, color, 0);
  lv_obj_set_style_bg_opa(object, LV_OPA_COVER, 0);
}

void create_eye(lv_disp_t *display, EyeView &eye)
{
  lv_obj_t *screen = lv_disp_get_scr_act(display);
  lv_obj_clean(screen);
  lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);

  eye.eyeball = lv_obj_create(screen);
  lv_obj_remove_style_all(eye.eyeball);
  lv_obj_clear_flag(eye.eyeball, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_size(eye.eyeball, EYE_WIDTH, EYE_HEIGHT);
  lv_obj_set_style_radius(eye.eyeball, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_color(eye.eyeball, lv_color_hex(0xF8FBFF), 0);
  lv_obj_set_style_bg_opa(eye.eyeball, LV_OPA_COVER, 0);
  lv_obj_set_style_clip_corner(eye.eyeball, true, 0);
  lv_obj_center(eye.eyeball);

  eye.iris = lv_obj_create(eye.eyeball);
  create_circle(eye.iris, IRIS_SIZE, lv_color_hex(0x39A8FF));
  lv_obj_align(eye.iris, LV_ALIGN_CENTER, gaze_x, gaze_y);

  lv_obj_t *pupil = lv_obj_create(eye.iris);
  create_circle(pupil, PUPIL_SIZE, lv_color_black());
  lv_obj_center(pupil);

  lv_obj_t *large_highlight = lv_obj_create(pupil);
  create_circle(large_highlight, 13, lv_color_white());
  lv_obj_align(large_highlight, LV_ALIGN_CENTER, -9, -9);

  lv_obj_t *small_highlight = lv_obj_create(pupil);
  create_circle(small_highlight, 6, lv_color_white());
  lv_obj_align(small_highlight, LV_ALIGN_CENTER, 9, 8);
}

void stop_existing_animation()
{
  if (controller_timer != NULL) {
    lv_timer_del(controller_timer);
    controller_timer = NULL;
  }
  lv_anim_del(&gaze_x, gaze_x_anim_cb);
  lv_anim_del(&gaze_y, gaze_y_anim_cb);
  lv_anim_del(&current_eye_height, eye_height_anim_cb);
}

bool send_command(const EyeCommand &command)
{
  return command_queue != NULL &&
         xQueueSend(command_queue, &command, 0) == pdTRUE;
}

}  // namespace

void Lvgl_Example1(void)
{
  stop_existing_animation();

  if (command_queue == NULL) {
    command_queue = xQueueCreate(8, sizeof(EyeCommand));
  } else {
    xQueueReset(command_queue);
  }

  gaze_x = 0;
  gaze_y = -4;
  current_eye_height = EYE_HEIGHT;
  base_eye_height = EYE_HEIGHT;
  left_eye = {};
  right_eye = {};
  create_eye(disp, left_eye);
  create_eye(disp2, right_eye);

  const uint32_t now = lv_tick_get();
  apply_state(EyeState::IDLE, now);
  next_gaze_at = now + random_between(500, 900);
  next_blink_at = now + random_between(1800, 3200);
  controller_timer = lv_timer_create(controller_timer_cb, 50, NULL);
}

bool Eye_RequestState(EyeState state)
{
  return send_command({EyeCommandType::STATE, state, 0, 0});
}

bool Eye_RequestGaze(int8_t x_percent, int8_t y_percent)
{
  return send_command({EyeCommandType::GAZE, EyeState::IDLE,
                       x_percent, y_percent});
}

bool Eye_RequestBlink()
{
  return send_command({EyeCommandType::BLINK, EyeState::IDLE, 0, 0});
}

void LVGL_Backlight_adjustment(uint8_t Backlight)
{
  Set_Backlight(Backlight);
}
