# Deskbot 外设接口与二次开发指南 (API Reference)

本文档面向基于 Deskbot 进行二次开发的工程师与智能体开发者，详细介绍**双轮运动控制**、**6轴陀螺仪姿态感知**与**视觉摄像头图像读取**三大核心外设的高层抽象接口、C++ API、调用示例及 MCP 大模型工具协议。

---

## 一、架构设计理念：统一 Board 门面 (Facade)

Deskbot 固件采用统一的 Board 门面模式。二次开发业务代码（如对话状态机、避障算法、视觉 Agent）无需关心具体的 GPIO 引脚编号、I2C 寄存器或底层的 PWM 定时器配置，只需通过全局单例 `Board::GetInstance()` 即可获取外设抽象控制器指针：

```cpp
#include "board.h"
#include "motion_controller.h"
#include "boards/common/qmi8658.h"
#include "boards/common/esp32_camera.h"

auto& board = Board::GetInstance();
MotionController* motion = board.GetMotionController(); // 双轮运动控制
Qmi8658*          imu    = board.GetImu();              // 6轴陀螺仪/加速度计
Camera*           camera = board.GetCamera();           // 摄像头抽象
```

所有返回的指针若在当前板级配置下未启用，均安全返回 `nullptr`，便于开发者在编写代码时进行空指针防护。

---

## 二、双轮运动控制接口 (`MotionController`)

### 1. 硬件特性与动力学映射
- **硬件驱动芯片**：PCA9685 16 通道 12-bit PWM 控制器（I2C 地址 `0x40`，PWM 基础频率 50Hz）。
- **电机类型**：SG5010 360° 连续旋转舵机（左轮 Channel 0，右轮 Channel 1）。
- **实测硬件死区校准**：
  - 硬件实测死区范围：`1510 µs ~ 1610 µs`
  - 静止中位脉冲：**`1560 µs`**（在此脉冲下舵机彻底静止，无微漂）
  - 死区半宽：`50 µs`，有效调速脉宽范围：`400 µs`
- **轮速抽象**：速度采用百分比标量 `-100 ~ +100`。
  - 正数：前进（+100 对应最大正向转速）
  - 负数：后退（-100 对应最大反向转速）
  - 0：完全刹车停机（输出 1560 µs）
- **安全自锁看门狗**：底层带有自动刹车定时器（默认超时自动归零停止），防止因网络中断或逻辑死锁导致小车失控撞墙。

---

### 2. 核心 C++ API 清单

头文件：`#include "motion_controller.h"`

```cpp
class MotionController {
public:
    virtual ~MotionController() = default;

    // --- 基础轮速控制接口 ---
    // 设置双轮速度百分比 [-100..100]
    virtual esp_err_t SetWheelSpeeds(int left_percent, int right_percent) = 0;
    virtual esp_err_t SetLeftSpeed(int percent) = 0;
    virtual esp_err_t SetRightSpeed(int percent) = 0;

    // --- 差速与线速度/角速度接口 ---
    // linear_speed: 前进速度 [-100..100], angular_turn: 转向分量 [-100..100]
    virtual esp_err_t Drive(int linear_speed, int angular_turn) = 0;

    // --- 带安全定时的动作接口 (duration_ms 后自动停止) ---
    virtual esp_err_t DriveForDuration(int left_percent, int right_percent, int duration_ms) = 0;
    virtual esp_err_t TurnForDuration(int angular_turn, int duration_ms) = 0;

    // --- 高层便捷方向控制接口 (极为推荐二次开发使用) ---
    // duration_ms: 运行毫秒数，若 > 0 则在执行完毕后自动刹车；若为 0 则持续运行
    esp_err_t Forward(int speed_percent = 20, int duration_ms = 0);
    esp_err_t Backward(int speed_percent = 20, int duration_ms = 0);
    esp_err_t TurnLeft(int speed_percent = 20, int duration_ms = 0);
    esp_err_t TurnRight(int speed_percent = 20, int duration_ms = 0);

    // --- 紧急制动接口 ---
    virtual esp_err_t Stop() = 0;

    // --- 状态与遥测查询 ---
    virtual int GetLeftSpeed() const = 0;
    virtual int GetRightSpeed() const = 0;
    virtual uint16_t GetLeftPulseUs() const = 0;
    virtual uint16_t GetRightPulseUs() const = 0;
};
```

---

### 3. 二次开发极简示例 (C++)

#### 示例 1：执行一段指定时间的运动序列（带安全刹车）
```cpp
#include "board.h"
#include "motion_controller.h"
#include <esp_log.h>

void RunMotionSequence() {
    auto motion = Board::GetInstance().GetMotionController();
    if (!motion) {
        ESP_LOGE("APP", "运动控制器未就绪！");
        return;
    }

    // 1. 以 30% 速度前进 1.5 秒后自动停下
    motion->Forward(30, 1500);
    vTaskDelay(pdMS_TO_TICKS(1600));

    // 2. 以 25% 速度原地向左转向 800 毫秒
    motion->TurnLeft(25, 800);
    vTaskDelay(pdMS_TO_TICKS(900));

    // 3. 停止所有轮子
    motion->Stop();
}
```

#### 示例 2：差速遥控或平滑避障转向
```cpp
void SmoothAvoidance(int target_left, int target_right) {
    auto motion = Board::GetInstance().GetMotionController();
    if (motion) {
        // 直接设置左右轮独立百分比（左轮 40%，右轮 15% 形成圆弧右转）
        motion->SetWheelSpeeds(target_left, target_right);
    }
}
```

---

### 4. 大模型 MCP 工具协议接口 (`self.motion.*`)

Deskbot 的运动接口已原生挂载至大模型 MCP Server，云端或本地大模型可直接下发 JSON-RPC 工具调用：

| MCP 工具名称 | 参数与格式 | 说明 |
| :--- | :--- | :--- |
| `self.motion.forward` | `{"speed": 30, "duration_ms": 1000}` | 前进指定时间（默认 30% 速度，1000ms） |
| `self.motion.backward` | `{"speed": 30, "duration_ms": 1000}` | 后退指定时间 |
| `self.motion.turn_left`| `{"speed": 30, "duration_ms": 500}`  | 原地左转 |
| `self.motion.turn_right`| `{"speed": 30, "duration_ms": 500}` | 原地右转 |
| `self.motion.drive`    | `{"left": 50, "right": 50, "duration_ms": 1000}` | 自定义左右轮独立百分比驱动 |
| `self.motion.stop`     | `{}` | 立即紧急刹车制动 |

---

## 三、6轴陀螺仪姿态感知接口 (`Qmi8658`)

### 1. 硬件特性与总线拓扑
- **传感器型号**：QMI8658 6轴惯性测量单元（3轴加速度计 + 3轴角速度陀螺仪）。
- **总线协议**：I2C1（SDA: GPIO 41, SCL: GPIO 42），从机地址 `0x6B`（或 `0x6A`）。
- **姿态解算**：驱动内置互补滤波算法，实时解算小车俯仰角（Pitch）与横滚角（Roll）。
- **物理量单位**：
  - 加速度：$g$（标准重力加速度，静止水平放置时 $a_z \approx 1.0$)
  - 角速度：$\text{dps}$（度/秒，转动时的即时旋转速率）
  - 姿态角：$\text{度}(^\circ)$

---

### 2. 核心 C++ API 清单

头文件：`#include "boards/common/qmi8658.h"`

```cpp
// 传感器遥测数据结构
struct ImuData {
    float ax = 0.0f;     // X轴加速度 (单位: g)
    float ay = 0.0f;     // Y轴加速度 (单位: g)
    float az = 0.0f;     // Z轴加速度 (单位: g)
    float gx = 0.0f;     // X轴角速度 (单位: dps)
    float gy = 0.0f;     // Y轴角速度 (单位: dps)
    float gz = 0.0f;     // Z轴角速度 (单位: dps)
    float pitch = 0.0f;  // 俯仰角 (单位: 度, 前倾为负/后仰为正)
    float roll = 0.0f;   // 横滚角 (单位: 度, 左倾为负/右倾为正)
};

class Qmi8658 {
public:
    // 读取最新的 6 轴与姿态融合数据 (线程安全)
    esp_err_t ReadData(ImuData& data);
    bool IsInitialized() const;
};
```

---

### 3. 二次开发极简示例 (C++)

#### 示例 1：小车翻倒/跌落安全检测与自锁
```cpp
#include "board.h"
#include "boards/common/qmi8658.h"
#include <cmath>

bool CheckIfFallenOrTilted() {
    auto imu = Board::GetInstance().GetImu();
    if (!imu) return false;

    ImuData data;
    if (imu->ReadData(data) == ESP_OK) {
        // 如果倾角超过 45 度，或者 Z 轴失重/颠倒
        if (std::abs(data.pitch) > 45.0f || std::abs(data.roll) > 45.0f || data.az < 0.2f) {
            // 立即停止小车电机，防止空转伤人
            if (auto motion = Board::GetInstance().GetMotionController()) {
                motion->Stop();
            }
            return true; // 异常姿态
        }
    }
    return false;
}
```

#### 示例 2：碰撞与剧烈震动事件触发
```cpp
void MonitorCollision() {
    auto imu = Board::GetInstance().GetImu();
    if (!imu) return;

    ImuData data;
    if (imu->ReadData(data) == ESP_OK) {
        // 计算水平加速度模长
        float horizontal_accel = std::sqrt(data.ax * data.ax + data.ay * data.ay);
        if (horizontal_accel > 1.8f) { // 超过 1.8g 判定为剧烈撞击
            ESP_LOGW("COLLISION", "检测到碰撞！合力=%.2fg", horizontal_accel);
            // 触发后退防卡死动作
            if (auto motion = Board::GetInstance().GetMotionController()) {
                motion->Backward(30, 600);
            }
        }
    }
}
```

---

### 4. 大模型 MCP 工具协议接口 (`self.sensor.*`)

大模型或外部客户端可通过 MCP 调用获取当前的实时姿态数据：

| MCP 工具名称 | 返回数据格式 | 说明 |
| :--- | :--- | :--- |
| `self.sensor.get_imu` | `{"ax": 0.02, "ay": -0.01, "az": 0.99, "gx": 0.1, "gy": 0.0, "gz": -0.2, "pitch": -0.5, "roll": 0.8}` | 读取当前 6 轴加速度、角速度与姿态角度 |

---

## 四、视觉摄像头图像接口 (`Esp32Camera`)

### 1. 硬件规格与驱动特性
- **传感器型号**：GC2145 DVP 摄像头（200 万像素 CMOS）。
- **总线架构**：8-bit 并行 DVP 接口，XCLK 20MHz，I2C 配置地址 `0x3C`。
- **图像格式**：320×240（QVGA），默认格式为 `PIXFORMAT_RGB565`（支持高速直接投屏至 ST7789 屏幕或实时 JPEG 编码传输）。
- **时序优化**：已解决 GC2145 PCLK 极性导致的花屏条纹，采用 SRAM 双缓冲与高吞吐 DMA 传输。

---

### 2. 核心 C++ API 清单

头文件：`#include "boards/common/esp32_camera.h"`

```cpp
class Esp32Camera : public Camera {
public:
    // 拍摄一帧最新画面并更新内部帧缓冲区 (线程安全)
    virtual bool Capture() override;

    // 获取当前捕获的原始帧缓冲结构指针
    camera_fb_t* GetCurrentFrameBuffer() const;

    // 图像翻转与字节序控制
    virtual bool SetHMirror(bool enabled) override;
    virtual bool SetVFlip(bool enabled) override;
    virtual bool SetSwapBytes(bool enabled) override;

    // 直接调用大模型视觉理解（将当前帧编码为 JPEG 并发送至视觉服务端）
    virtual std::expected<std::string, std::string> Explain(const std::string& question) override;
};
```

帧缓冲区 `camera_fb_t` 结构体字段说明：
```cpp
typedef struct {
    uint8_t * buf;        // 指向原始像素数据的指针 (RGB565 或 JPEG)
    size_t len;           // 缓冲区总字节数 (例如 320x240x2 = 153,600 字节)
    size_t width;         // 图像宽度 (320)
    size_t height;        // 图像高度 (240)
    pixformat_t format;   // 像素格式 (PIXFORMAT_RGB565 或 PIXFORMAT_JPEG)
    struct timeval timestamp; // 抓帧时间戳
} camera_fb_t;
```

---

### 3. 二次开发极简示例 (C++)

#### 示例 1：获取单帧数据并进行图像灰度化/目标检测
```cpp
#include "board.h"
#include "boards/common/esp32_camera.h"
#include <esp_log.h>

void ProcessCameraFrame() {
    auto camera = Board::GetInstance().GetCamera();
    if (!camera) {
        ESP_LOGE("CAM", "摄像头未初始化");
        return;
    }

    // 1. 抓取最新一帧
    if (!camera->Capture()) {
        ESP_LOGW("CAM", "抓帧超时");
        return;
    }

    // 2. 获取帧缓冲指针
    auto esp_cam = static_cast<Esp32Camera*>(camera);
    camera_fb_t* fb = esp_cam->GetCurrentFrameBuffer();
    if (!fb || !fb->buf) return;

    ESP_LOGI("CAM", "成功抓帧: %dx%d, 大小: %u 字节", fb->width, fb->height, fb->len);

    // 3. 遍历 RGB565 像素示例 (320x240)
    uint16_t* pixels = reinterpret_cast<uint16_t*>(fb->buf);
    int total_pixels = fb->width * fb->height;
    uint32_t total_luma = 0;

    for (int i = 0; i < total_pixels; i++) {
        uint16_t rgb = pixels[i];
        // 提取 R(5bit), G(6bit), B(5bit)
        uint8_t r = (rgb >> 11) & 0x1F;
        uint8_t g = (rgb >> 5) & 0x3F;
        uint8_t b = rgb & 0x1F;
        total_luma += (r * 2 + g + b * 2);
    }
    ESP_LOGI("CAM", "画面平均亮度指标: %u", total_luma / total_pixels);
}
```

#### 示例 2：一键调用大模型进行多模态视觉问答
```cpp
void AskAiAboutCurrentView() {
    auto camera = Board::GetInstance().GetCamera();
    if (!camera) return;

    // 向视觉大模型提问（固件自动抓帧、转码为 JPEG 并发起 HTTP/WebSocket 协议传输）
    auto result = camera->Explain("画面正前方是否有障碍物？有的话大概在什么方位？");
    if (result.has_value()) {
        ESP_LOGI("AI_VISION", "大模型回答: %s", result.value().c_str());
    } else {
        ESP_LOGE("AI_VISION", "视觉分析失败: %s", result.error().c_str());
    }
}
```

---

## 五、综合实战：构建自主避障与视觉巡航循环

下面展示如何将**运动控制**、**陀螺仪姿态**与**摄像头**组合在一起，实现一个典型的自主智能体工作循环：

```cpp
void AutonomousAgentTask(void* pvParameters) {
    auto& board = Board::GetInstance();
    auto motion = board.GetMotionController();
    auto imu = board.GetImu();
    auto camera = board.GetCamera();

    while (true) {
        // 1. 姿态安全监测
        if (imu) {
            ImuData imu_data;
            if (imu->ReadData(imu_data) == ESP_OK) {
                if (std::abs(imu_data.pitch) > 30.0f) {
                    ESP_LOGW("AGENT", "路面陡峭，停止前进");
                    motion->Stop();
                    vTaskDelay(pdMS_TO_TICKS(1000));
                    continue;
                }
            }
        }

        // 2. 视觉环境感知
        if (camera) {
            auto answer = camera->Explain("如果正前方可以通行请回答 GO，如果有障碍物请回答 BLOCKED");
            if (answer.has_value() && answer.value().find("BLOCKED") != std::string::npos) {
                // 遇到障碍物：后退并右转
                motion->Backward(25, 600);
                vTaskDelay(pdMS_TO_TICKS(700));
                motion->TurnRight(30, 800);
                vTaskDelay(pdMS_TO_TICKS(900));
                continue;
            }
        }

        // 3. 无障碍物：正常前进 1 秒
        if (motion) {
            motion->Forward(25, 1000);
        }

        vTaskDelay(pdMS_TO_TICKS(1100));
    }
}
```

---

## 六、开发注意事项与调试技巧

1. **空指针保护**：使用任何外设指针前，务必检查是否为 `nullptr`（防止某些轻量分支未配置外设时崩溃）。
2. **总线互斥锁**：I2C1 总线挂载了音频编解码器、触摸屏、陀螺仪与舵机驱动芯片。固件内部各驱动已使用互斥锁或串行化调度，二次开发如需直接发起裸 I2C 事务，请使用 `I2cBusLockGuard`。
3. **安全自锁**：连续旋转舵机在机械结构上无法反馈绝对角度，驱动层内置了超时安全看门狗。对于长时间运动，必须循环续订或使用高层带 `duration_ms` 的接口，严禁发出无限期高速运转指令。
