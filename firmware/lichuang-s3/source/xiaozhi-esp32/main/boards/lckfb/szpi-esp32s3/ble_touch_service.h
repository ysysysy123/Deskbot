#ifndef BLE_TOUCH_SERVICE_H
#define BLE_TOUCH_SERVICE_H

#include <cstdint>
#include <cstddef>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <freertos/timers.h>
#include <lvgl.h>
#include <esp_err.h>

class MotionController;
class Qmi8658;
class Esp32Camera;

/**
 * Binary protocol message types
 */
enum BleMsgType : uint8_t {
    BLE_MSG_TOUCH       = 0x01,  // Mobile -> ESP32: [action(1), x(2), y(2)]
    BLE_MSG_SERVO       = 0x02,  // Mobile -> ESP32: [left(1), right(1), duration_ms(2)]
    BLE_MSG_HEARTBEAT   = 0x03,  // Mobile <-> ESP32: [timestamp(4)]
    BLE_MSG_SNAP_REQ    = 0x04,  // Mobile -> ESP32: [source(1)] 0=Screen, 1=Camera
    BLE_MSG_TELEMETRY   = 0x81,  // ESP32 -> Mobile: 10Hz Telemetry
    BLE_MSG_IMAGE_CHUNK = 0x82,  // ESP32 -> Mobile: Image chunk
    BLE_MSG_PONG        = 0x83   // ESP32 -> Mobile: [timestamp(4)]
};

enum BleTouchAction : uint8_t {
    BLE_TOUCH_DOWN = 0,
    BLE_TOUCH_MOVE = 1,
    BLE_TOUCH_UP   = 2
};

struct BleTouchEvent {
    BleTouchAction action;
    int16_t x;
    int16_t y;
};

class BleTouchService {
public:
    static BleTouchService& GetInstance();

    esp_err_t Start(MotionController* motion, Qmi8658* imu = nullptr, Esp32Camera* camera = nullptr);
    void Stop();

    void RegisterVirtualTouchIndev();
    void SetCamera(Esp32Camera* camera) { camera_ = camera; }
    void SetImu(Qmi8658* imu) { imu_ = imu; }
    void SetMotion(MotionController* motion) { motion_ = motion; }

    bool IsConnected() const { return is_connected_; }

    // Dispatcher methods called from GATT RX
    void OnReceiveRxData(const uint8_t* data, size_t len);
    void OnConnected(uint16_t conn_handle);
    void OnDisconnected();
    void OnMtuUpdated(uint16_t mtu);

    // Watchdog
    void FeedWatchdog();

private:
    BleTouchService();
    ~BleTouchService();

    void InitNimble();
    static void NimbleHostTask(void* param);
    static void TelemetryTask(void* param);
    static void ImageTask(void* param);
    static void WatchdogTimerCb(TimerHandle_t timer);
    static void LvglTouchReadCallback(lv_indev_t* indev, lv_indev_data_t* data);

    esp_err_t SendNotifyPacket(const uint8_t* data, size_t len);
    void SendPong(uint32_t timestamp);
    void TriggerSnapshot(uint8_t source);

    MotionController* motion_ = nullptr;
    Qmi8658* imu_ = nullptr;
    Esp32Camera* camera_ = nullptr;

    bool is_connected_ = false;
    uint16_t conn_handle_ = 0;
    uint16_t mtu_ = 512;
    uint16_t tx_val_handle_ = 0;

    QueueHandle_t touch_queue_ = nullptr;
    TimerHandle_t watchdog_timer_ = nullptr;
    TaskHandle_t telemetry_task_ = nullptr;
    TaskHandle_t image_task_ = nullptr;
    SemaphoreHandle_t snap_sem_ = nullptr;
    SemaphoreHandle_t ble_tx_mutex_ = nullptr;
    std::atomic<bool> is_transferring_image_{false};

    uint8_t snap_source_ = 0;
    uint8_t snap_quality_ = 35;
    uint16_t frame_seq_ = 0;

    // LVGL input device state
    lv_indev_t* virtual_indev_ = nullptr;
    int16_t current_x_ = 0;
    int16_t current_y_ = 0;
    lv_indev_state_t current_state_ = LV_INDEV_STATE_RELEASED;
    TickType_t last_down_tick_ = 0;

    friend int BleGattSvcAccess(uint16_t conn_handle, uint16_t attr_handle,
                                struct ble_gatt_access_ctxt *ctxt, void *arg);
};

#endif // BLE_TOUCH_SERVICE_H
