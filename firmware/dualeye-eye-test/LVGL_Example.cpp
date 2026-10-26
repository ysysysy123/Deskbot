// ============================================================
//  LVGL_Example.cpp  —— 九种可辨眼型版本
//
//  与旧版的区别：
//    旧版：只改「虹膜大小 + 颜色 + 眼球高度」，生气/难过只有颜色和抖动，
//          文档 4.3 明确判定不合格。
//    新版：用 LVGL Canvas 逐像素渲染，眼睑是带【斜率 + 弧度】的曲线，
//          能画出斜眼睑（生气）、下垂眼睑（难过）、笑眼弧（开心）、
//          瞪大（惊讶），眼型本身可辨，不只靠颜色。
//
//  对外 API 完全不变（Eye_RequestState / Eye_RequestGaze / Eye_RequestBlink），
//  Eye_UART.cpp 和 dualeye-eye-test.ino 不需要改。
// ============================================================

#include "LVGL_Example.h"

#include <esp_heap_caps.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

namespace {

// ---------------- 画布尺寸 ----------------
constexpr int16_t EYE_W = 176;          // 每只眼画布宽
constexpr int16_t EYE_H = 164;          // 高
constexpr int16_t EYE_CX = EYE_W / 2;   // 中心 x
constexpr int16_t EYE_CY = EYE_H / 2;   // 中心 y
constexpr int16_t SCLERA_RX = EYE_W / 2 - 1;  // 巩膜半轴 x
constexpr int16_t SCLERA_RY = EYE_H / 2 - 1;  // 巩膜半轴 y

// ---------------- 眼型参数 ----------------
// 眼睑边缘公式（dy 为相对眼球中心的纵坐标，向下为正）：
//   edge(x) = base + slope * xm / 100 + curve * xm * xm / 100000
//   xm = (x - 中心) * mirror          （右眼镜像，斜率自动反向）
// 上眼睑「以上」被遮黑，下眼睑「以下」被遮黑；留出的带子就是可见眼缝。
struct EyeStyle {
  int16_t iris;        // 虹膜直径
  int16_t pupil;       // 瞳孔直径
  uint32_t iris_color; // 虹膜色
  int16_t top_base;    // 上眼睑基准
  int16_t top_slope;   // 上眼睑斜率（*100）
  int16_t top_curve;   // 上眼睑弧度（分母 100000）
  int16_t bot_base;    // 下眼睑基准
  int16_t bot_slope;   // 下眼睑斜率
  int16_t bot_curve;   // 下眼睑弧度
  bool    closed;      // 闭眼（睡眠）
};

EyeStyle style_for(EyeState state) {
  switch (state) {
    // 待机：完全睁开，正常瞳色
    case EyeState::IDLE:
      return {74, 40, 0x39A8FF, -82, 0, 0, 82, 0, 0, false};

    // 聆听：睁得更开 + 虹膜放大（专注感），与待机明显区分
    case EyeState::LISTENING:
      return {88, 46, 0x26D9C7, -86, 0, 0, 86, 0, 0, false};

    // 思考：上眼睑微垂（半眯），配合斜上方视线
    case EyeState::THINKING:
      return {66, 36, 0xA66CFF, -56, 10, -40, 80, 0, 0, false};

    // 说话：接近常态、轻微动态
    case EyeState::SPEAKING:
      return {78, 42, 0x39D5FF, -76, 0, 0, 78, 0, 0, false};

    // 开心：笑眼 —— 上下眼睑都抬升并带强弧度，眼缝变成上翘的月牙
    case EyeState::HAPPY:
      return {72, 38, 0xFFC84A, -14, 0, -430, 12, 0, -430, false};

    // 难过：上眼睑【外侧下垂】，呈下垂眼型，配合向下视线
    case EyeState::SAD:
      return {64, 34, 0x6B7BD9, -44, -42, 55, 46, 0, 0, false};

    // 生气：上眼睑【内侧下压】形成斜眼，下眼睑略抬
    case EyeState::ANGRY:
      return {60, 32, 0xFF5A4E, -46, 50, 0, 30, 0, 0, false};

    // 惊讶：睁到最大 + 虹膜缩小（露白更多），眼型变圆
    case EyeState::SURPRISED:
      return {56, 32, 0xFFD75A, -90, 0, 0, 90, 0, 0, false};

    // 睡眠：闭眼
    case EyeState::SLEEPING:
      return {0, 0, 0x000000, 0, 0, 0, 0, 0, 0, true};
  }
  return {74, 40, 0x39A8FF, -82, 0, 0, 82, 0, 0, false};
}

struct EyeCanvas {
  lv_obj_t* canvas = nullptr;
  lv_color_t* buf = nullptr;
  int8_t mirror = 1;  // +1 左眼，-1 右眼（镜像斜率）
};

EyeCanvas left_eye;
EyeCanvas right_eye;

lv_timer_t* controller_timer = nullptr;
QueueHandle_t command_queue = nullptr;

EyeState current_state = EyeState::IDLE;
EyeStyle style_from = style_for(EyeState::IDLE);
EyeStyle style_to = style_for(EyeState::IDLE);
int32_t morph = 1000;  // 0..1000，眼型过渡进度

int32_t gaze_x = 0;
int32_t gaze_y = -4;
int32_t blink_px = 0;  // 眨眼额外遮挡高度

uint32_t next_gaze_at = 0;
uint32_t next_blink_at = 0;
uint32_t manual_gaze_until = 0;
bool thinking_looks_right = false;
bool speaking_moves_down = false;
bool dirty = true;

// ---------------- 工具 ----------------
bool time_reached(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

uint32_t random_between(uint32_t minimum, uint32_t maximum) {
  return minimum + esp_random() % (maximum - minimum);
}

int16_t lerp16(int16_t a, int16_t b, int32_t t) {
  return static_cast<int16_t>(a + (static_cast<int32_t>(b - a) * t) / 1000);
}

uint32_t lerp_color(uint32_t a, uint32_t b, int32_t t) {
  const int32_t ar = (a >> 16) & 0xFF, ag = (a >> 8) & 0xFF, ab = a & 0xFF;
  const int32_t br = (b >> 16) & 0xFF, bg = (b >> 8) & 0xFF, bb = b & 0xFF;
  const int32_t r = ar + (br - ar) * t / 1000;
  const int32_t g = ag + (bg - ag) * t / 1000;
  const int32_t bl = ab + (bb - ab) * t / 1000;
  return (static_cast<uint32_t>(r) << 16) | (static_cast<uint32_t>(g) << 8) |
         static_cast<uint32_t>(bl);
}

EyeStyle current_style() {
  EyeStyle s = style_to;
  if (morph >= 1000) return s;
  s.iris = lerp16(style_from.iris, style_to.iris, morph);
  s.pupil = lerp16(style_from.pupil, style_to.pupil, morph);
  s.iris_color = lerp_color(style_from.iris_color, style_to.iris_color, morph);
  s.top_base = lerp16(style_from.top_base, style_to.top_base, morph);
  s.top_slope = lerp16(style_from.top_slope, style_to.top_slope, morph);
  s.top_curve = lerp16(style_from.top_curve, style_to.top_curve, morph);
  s.bot_base = lerp16(style_from.bot_base, style_to.bot_base, morph);
  s.bot_slope = lerp16(style_from.bot_slope, style_to.bot_slope, morph);
  s.bot_curve = lerp16(style_from.bot_curve, style_to.bot_curve, morph);
  s.closed = style_to.closed;
  return s;
}

// ---------------- 渲染 ----------------
void draw_closed_eyelid(lv_color_t* buf, uint32_t line_color) {
  const lv_color_t c = lv_color_hex(line_color);
  for (int16_t x = 24; x < EYE_W - 24; ++x) {
    const int16_t dx = x - EYE_CX;
    // 轻微向上翘的弧线，像闭着的眼睛
    const int16_t y = EYE_CY + 10 - static_cast<int16_t>(dx * dx / 480);
    for (int16_t t = -3; t <= 3; ++t) {
      const int16_t yy = y + t;
      if (yy >= 0 && yy < EYE_H) buf[yy * EYE_W + x] = c;
    }
  }
}

void render_eye(EyeCanvas& eye, const EyeStyle& st, int32_t gx, int32_t gy,
                int32_t extra_cover) {
  if (eye.buf == nullptr) return;

  lv_color_t* buf = eye.buf;
  const lv_color_t bg = lv_color_black();

  // 1) 清屏
  for (int32_t i = 0; i < static_cast<int32_t>(EYE_W) * EYE_H; ++i) buf[i] = bg;

  // 2) 闭眼
  if (st.closed) {
    draw_closed_eyelid(buf, 0x8FA6C8);
    return;
  }

  // 3) 预计算每列眼缝上下边界（dy 空间）
  static int16_t top_dy[EYE_W];
  static int16_t bot_dy[EYE_W];
  for (int16_t x = 0; x < EYE_W; ++x) {
    const int32_t dx = static_cast<int32_t>(x) - EYE_CX;
    const int32_t xm = dx * eye.mirror;
    const int32_t t = st.top_base + static_cast<int32_t>(st.top_slope) * xm / 100 +
                      static_cast<int32_t>(st.top_curve) * xm * xm / 100000 -
                      extra_cover;
    const int32_t b = st.bot_base + static_cast<int32_t>(st.bot_slope) * xm / 100 +
                      static_cast<int32_t>(st.bot_curve) * xm * xm / 100000;
    top_dy[x] = static_cast<int16_t>(t < -EYE_CY ? -EYE_CY : (t > EYE_CY ? EYE_CY : t));
    bot_dy[x] = static_cast<int16_t>(b < -EYE_CY ? -EYE_CY : (b > EYE_CY ? EYE_CY : b));
  }

  // 4) 画眼球 + 虹膜 + 瞳孔 + 高光
  const lv_color_t sclera = lv_color_hex(0xF8FBFF);
  const lv_color_t iris_c = lv_color_hex(st.iris_color);
  const lv_color_t pupil_c = lv_color_black();
  const lv_color_t white = lv_color_white();

  const int32_t rx2 = SCLERA_RX * SCLERA_RX;
  const int32_t ry2 = SCLERA_RY * SCLERA_RY;
  const int32_t sclera_limit = rx2 * ry2;

  const int32_t icx = EYE_CX + gx;
  const int32_t icy = EYE_CY + gy;
  const int32_t ir = st.iris / 2;
  const int32_t pr = st.pupil / 2;
  const int32_t ir2 = ir * ir;
  const int32_t pr2 = pr * pr;

  for (int16_t y = 0; y < EYE_H; ++y) {
    const int32_t dy = static_cast<int32_t>(y) - EYE_CY;
    lv_color_t* row = buf + static_cast<int32_t>(y) * EYE_W;
    for (int16_t x = 0; x < EYE_W; ++x) {
      if (dy < top_dy[x] || dy > bot_dy[x]) continue;  // 落在眼睑外
      const int32_t dx = static_cast<int32_t>(x) - EYE_CX;
      if (dx * dx * ry2 + dy * dy * rx2 > sclera_limit) continue;  // 巩膜椭圆外

      lv_color_t c = sclera;
      const int32_t ix = static_cast<int32_t>(x) - icx;
      const int32_t iy = static_cast<int32_t>(y) - icy;
      const int32_t d2 = ix * ix + iy * iy;

      if (d2 <= pr2) {
        c = pupil_c;
        const int32_t hx = ix + 9, hy = iy + 9;      // 大高光
        const int32_t sx = ix - 9, sy2 = iy - 8;     // 小高光
        if (hx * hx + hy * hy <= 49) c = white;
        if (sx * sx + sy2 * sy2 <= 16) c = white;
      } else if (d2 <= ir2) {
        c = iris_c;
      }
      row[x] = c;
    }
  }
}

void redraw_all() {
  const EyeStyle st = current_style();
  render_eye(left_eye, st, gaze_x, gaze_y, blink_px);
  render_eye(right_eye, st, gaze_x, gaze_y, blink_px);
  if (left_eye.canvas) lv_obj_invalidate(left_eye.canvas);
  if (right_eye.canvas) lv_obj_invalidate(right_eye.canvas);
  dirty = false;
}

// ---------------- 动画 ----------------
void gaze_x_anim_cb(void*, int32_t value) {
  gaze_x = value;
  dirty = true;
}
void gaze_y_anim_cb(void*, int32_t value) {
  gaze_y = value;
  dirty = true;
}
void morph_anim_cb(void*, int32_t value) {
  morph = value;
  dirty = true;
}
void blink_anim_cb(void*, int32_t value) {
  blink_px = value;
  dirty = true;
}

void start_anim(void* var, int32_t from, int32_t to, uint32_t ms,
                lv_anim_exec_xcb_t cb, bool yoyo) {
  lv_anim_del(var, cb);
  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, var);
  lv_anim_set_values(&a, from, to);
  lv_anim_set_time(&a, ms);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
  lv_anim_set_exec_cb(&a, cb);
  if (yoyo) {
    lv_anim_set_playback_delay(&a, 30);
    lv_anim_set_playback_time(&a, ms);
  }
  lv_anim_start(&a);
}

void start_gaze(int32_t x, int32_t y, uint32_t ms) {
  start_anim(&gaze_x, gaze_x, x, ms, gaze_x_anim_cb, false);
  start_anim(&gaze_y, gaze_y, y, ms, gaze_y_anim_cb, false);
}

void start_blink(uint32_t now) {
  if (current_state == EyeState::SLEEPING) return;
  start_anim(&blink_px, 0, 150, 80, blink_anim_cb, true);
  next_blink_at = now + random_between(2800, 6500);
}

void start_random_gaze(uint32_t now) {
  static const int8_t targets[][2] = {
      {0, -6}, {-28, -7}, {28, -7}, {-18, 8}, {18, 8}, {0, 10},
  };
  const size_t i = esp_random() % (sizeof(targets) / sizeof(targets[0]));
  start_gaze(targets[i][0], targets[i][1], random_between(280, 560));
  next_gaze_at = now + random_between(900, 2600);
}

// ---------------- 状态 ----------------
void apply_state(EyeState state, uint32_t now) {
  // 交接文档 4.3：重复收到同一状态时不得不断重置眨眼/视线计时，否则动画会卡住。
  // 只有在"状态真的变了"或"上一次过渡还没走完"时才重新计时。
  if (state == current_state && morph >= 1000) {
    dirty = true;
    return;
  }

  current_state = state;
  manual_gaze_until = 0;
  thinking_looks_right = false;
  speaking_moves_down = false;
  blink_px = 0;

  style_from = current_style();
  style_to = style_for(state);
  morph = 0;
  start_anim(&morph, 0, 1000, 200, morph_anim_cb, false);
  dirty = true;

  switch (state) {
    case EyeState::THINKING:
      start_gaze(-24, -6, 220);
      break;
    case EyeState::HAPPY:
      start_gaze(0, 5, 220);
      break;
    case EyeState::SAD:
      start_gaze(0, 12, 260);
      break;
    case EyeState::ANGRY:
      start_gaze(0, -6, 180);
      break;
    case EyeState::SURPRISED:
      start_gaze(0, -8, 200);
      break;
    case EyeState::SLEEPING:
      start_gaze(0, 0, 200);
      break;
    default:
      start_gaze(0, -4, 220);
      break;
  }

  next_gaze_at = now + 600;
  next_blink_at = now + random_between(2200, 4200);
}

void apply_manual_gaze(int8_t x_percent, int8_t y_percent, uint32_t now) {
  start_gaze(static_cast<int32_t>(x_percent) * 30 / 100,
             static_cast<int32_t>(y_percent) * 14 / 100, 180);
  manual_gaze_until = now + 2000;
}

// ---------------- 命令队列 ----------------
enum class EyeCommandType : uint8_t { STATE, GAZE, BLINK };

struct EyeCommand {
  EyeCommandType type;
  EyeState state;
  int8_t x;
  int8_t y;
};

bool send_command(const EyeCommand& command) {
  return command_queue != nullptr &&
         xQueueSend(command_queue, &command, 0) == pdTRUE;
}

void process_commands(uint32_t now) {
  if (command_queue == nullptr) return;
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

// ---------------- 自动动作 ----------------
void update_automatic_motion(uint32_t now) {
  if (manual_gaze_until != 0 && !time_reached(now, manual_gaze_until)) {
    if (time_reached(now, next_blink_at)) start_blink(now);
    return;
  }
  manual_gaze_until = 0;

  switch (current_state) {
    case EyeState::IDLE:
      if (time_reached(now, next_gaze_at)) start_random_gaze(now);
      break;
    case EyeState::LISTENING:
      if (time_reached(now, next_gaze_at)) {
        start_gaze(0, -3, 180);
        next_gaze_at = now + 1400;
      }
      break;
    case EyeState::THINKING:  // 有节奏的左右 / 斜上方视线
      if (time_reached(now, next_gaze_at)) {
        thinking_looks_right = !thinking_looks_right;
        start_gaze(thinking_looks_right ? 26 : -26, thinking_looks_right ? -8 : -4, 260);
        next_gaze_at = now + 520;
      }
      break;
    case EyeState::SPEAKING:  // 连贯轻微上下，不剧烈抖动
      if (time_reached(now, next_gaze_at)) {
        speaking_moves_down = !speaking_moves_down;
        start_gaze(0, speaking_moves_down ? 4 : -7, 190);
        next_gaze_at = now + 320;
      }
      break;
    case EyeState::HAPPY:
      if (time_reached(now, next_gaze_at)) {
        start_gaze(0, 5, 200);
        next_gaze_at = now + 1600;
      }
      break;
    case EyeState::SAD:
      if (time_reached(now, next_gaze_at)) {
        start_gaze(0, 12, 340);
        next_gaze_at = now + 2000;
      }
      break;
    case EyeState::ANGRY:  // 缓慢小幅，不抖（抖动交给眼型表达）
      if (time_reached(now, next_gaze_at)) {
        start_gaze(0, -6, 300);
        next_gaze_at = now + 1800;
      }
      break;
    case EyeState::SURPRISED:
      if (time_reached(now, next_gaze_at)) {
        start_gaze(0, -8, 240);
        next_gaze_at = now + 2200;
      }
      break;
    case EyeState::SLEEPING:
      return;  // 闭眼不眨眼、不扫视
  }

  if (time_reached(now, next_blink_at)) start_blink(now);
}

void controller_timer_cb(lv_timer_t*) {
  const uint32_t now = lv_tick_get();
  process_commands(now);
  update_automatic_motion(now);
  if (dirty) redraw_all();
}

// ---------------- 初始化 ----------------
void* alloc_eye_buffer(size_t bytes) {
  void* p = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (p == nullptr) {
    p = heap_caps_malloc(bytes, MALLOC_CAP_8BIT);  // 无 PSRAM 时退回内部 RAM
  }
  return p;
}

void create_eye_canvas(lv_disp_t* display, EyeCanvas& eye, int8_t mirror) {
  lv_obj_t* screen = lv_disp_get_scr_act(display);
  lv_obj_clean(screen);
  lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);

  if (eye.buf == nullptr) {
    eye.buf = static_cast<lv_color_t*>(alloc_eye_buffer(
        sizeof(lv_color_t) * EYE_W * EYE_H));
  }

  eye.canvas = lv_canvas_create(screen);
  if (eye.buf != nullptr) {
    lv_canvas_set_buffer(eye.canvas, eye.buf, EYE_W, EYE_H, LV_IMG_CF_TRUE_COLOR);
  }
  lv_obj_center(eye.canvas);
  eye.mirror = mirror;
}

void stop_existing_animation() {
  if (controller_timer != nullptr) {
    lv_timer_del(controller_timer);
    controller_timer = nullptr;
  }
  lv_anim_del(&gaze_x, gaze_x_anim_cb);
  lv_anim_del(&gaze_y, gaze_y_anim_cb);
  lv_anim_del(&morph, morph_anim_cb);
  lv_anim_del(&blink_px, blink_anim_cb);
}

}  // namespace

// ================= 对外 API =================
void Lvgl_Example1(void) {
  stop_existing_animation();

  if (command_queue == nullptr) {
    command_queue = xQueueCreate(8, sizeof(EyeCommand));
  } else {
    xQueueReset(command_queue);
  }

  gaze_x = 0;
  gaze_y = -4;
  blink_px = 0;
  morph = 1000;
  left_eye.canvas = nullptr;
  right_eye.canvas = nullptr;
  create_eye_canvas(disp, left_eye, +1);
  create_eye_canvas(disp2, right_eye, -1);

  const uint32_t now = lv_tick_get();
  current_state = EyeState::IDLE;
  style_from = style_for(EyeState::IDLE);
  style_to = style_for(EyeState::IDLE);
  morph = 1000;
  dirty = true;
  redraw_all();

  next_gaze_at = now + random_between(500, 900);
  next_blink_at = now + random_between(1800, 3200);
  controller_timer = lv_timer_create(controller_timer_cb, 30, nullptr);
}

bool Eye_RequestState(EyeState state) {
  return send_command({EyeCommandType::STATE, state, 0, 0});
}

bool Eye_RequestGaze(int8_t x_percent, int8_t y_percent) {
  return send_command({EyeCommandType::GAZE, EyeState::IDLE, x_percent, y_percent});
}

bool Eye_RequestBlink() {
  return send_command({EyeCommandType::BLINK, EyeState::IDLE, 0, 0});
}

void LVGL_Backlight_adjustment(uint8_t Backlight) {
  Set_Backlight(Backlight);
}
