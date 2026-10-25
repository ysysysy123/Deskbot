#include "ble_touch_service.h"
#include "motion_controller.h"
#include "qmi8658.h"
#include "esp32_camera.h"
#include "jpg/image_to_jpeg.h"

#include <esp_log.h>
#include <esp_timer.h>
#include <esp_lvgl_port.h>
#include <cstring>
#include <algorithm>

#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#if LV_USE_SNAPSHOT
#include "src/draw/snapshot/lv_snapshot.h"
#endif

#define TAG "BleTouchService"
#define DEVICE_NAME "Deskbot-Touch"

// Nordic UART Service (NUS)
// Service: 6E400001-B5A3-F393-E0A9-E50E24DCCA9E
static const ble_uuid128_t g_nus_svc_uuid =
    BLE_UUID128_INIT(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                     0x93, 0xf3, 0xa3, 0xb5, 0x01, 0x00, 0x40, 0x6e);

// RX Char (Mobile -> ESP32, Write)
// 6E400002-B5A3-F393-E0A9-E50E24DCCA9E
static const ble_uuid128_t g_nus_rx_uuid =
    BLE_UUID128_INIT(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                     0x93, 0xf3, 0xa3, 0xb5, 0x02, 0x00, 0x40, 0x6e);

// TX Char (ESP32 -> Mobile, Notify)
// 6E400003-B5A3-F393-E0A9-E50E24DCCA9E
static const ble_uuid128_t g_nus_tx_uuid =
    BLE_UUID128_INIT(0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
                     0x93, 0xf3, 0xa3, 0xb5, 0x03, 0x00, 0x40, 0x6e);

static uint16_t g_tx_val_handle = 0;

static uint16_t CalcCRC16(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; ++i) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; ++j) {
            if (crc & 0x8000) {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

int BleGattSvcAccess(uint16_t conn_handle, uint16_t attr_handle,
                     struct ble_gatt_access_ctxt *ctxt, void *arg) {
    auto& svc = BleTouchService::GetInstance();
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        uint8_t buf[512];
        uint16_t out_len = 0;
        int rc = ble_hs_mbuf_to_flat(ctxt->om, buf, sizeof(buf), &out_len);
        if (rc == 0 && out_len > 0) {
            svc.OnReceiveRxData(buf, out_len);
        }
        return 0;
    }
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        return 0;
    }
    return 0;
}

static const struct ble_gatt_svc_def g_gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &g_nus_svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                // RX Characteristic: Write / Write Without Response
                .uuid = &g_nus_rx_uuid.u,
                .access_cb = BleGattSvcAccess,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                // TX Characteristic: Notify (Standard NUS)
                .uuid = &g_nus_tx_uuid.u,
                .access_cb = BleGattSvcAccess,
                .flags = BLE_GATT_CHR_F_NOTIFY,
                .val_handle = &g_tx_val_handle,
            },
            {0}
        },
    },
    {0}
};

static int GapEventHandler(struct ble_gap_event *event, void *arg);

static void StartAdvertising() {
    struct ble_gap_adv_params adv_params = {};
    adv_params.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv_params.disc_mode = BLE_GAP_DISC_MODE_GEN;

    // 1. Primary Advertising Packet: Flags + Device Name (18 bytes <= 31 byte limit)
    struct ble_hs_adv_fields fields = {};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)DEVICE_NAME;
    fields.name_len = strlen(DEVICE_NAME);
    fields.name_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gap_adv_set_fields failed: rc=%d", rc);
        return;
    }

    // 2. Scan Response Packet: 128-bit NUS Service UUID (18 bytes <= 31 byte limit)
    struct ble_hs_adv_fields rsp_fields = {};
    rsp_fields.uuids128 = (ble_uuid128_t*)&g_nus_svc_uuid;
    rsp_fields.num_uuids128 = 1;
    rsp_fields.uuids128_is_complete = 1;

    rc = ble_gap_adv_rsp_set_fields(&rsp_fields);
    if (rc != 0) {
        ESP_LOGW(TAG, "ble_gap_adv_rsp_set_fields failed: rc=%d", rc);
    }

    rc = ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC, NULL, BLE_HS_FOREVER,
                           &adv_params, GapEventHandler, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gap_adv_start failed: rc=%d", rc);
    } else {
        ESP_LOGI(TAG, "BLE advertising started: %s", DEVICE_NAME);
    }
}

static int GapEventHandler(struct ble_gap_event *event, void *arg) {
    auto& svc = BleTouchService::GetInstance();
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            ESP_LOGI(TAG, "BLE connected, conn_handle=%d", event->connect.conn_handle);
            svc.OnConnected(event->connect.conn_handle);
        } else {
            ESP_LOGW(TAG, "BLE connection failed, restart adv");
            StartAdvertising();
        }
        break;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "BLE disconnected, reason=%d", event->disconnect.reason);
        svc.OnDisconnected();
        StartAdvertising();
        break;

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "BLE MTU updated: %d", event->mtu.value);
        svc.OnMtuUpdated(event->mtu.value);
        break;

    default:
        break;
    }
    return 0;
}

static void OnSync(void) {
    ESP_LOGI(TAG, "NimBLE host synced, starting adv");
    StartAdvertising();
}

static void OnReset(int reason) {
    ESP_LOGE(TAG, "NimBLE host reset, reason=%d", reason);
}

// -------------------------------------------------------------
// BleTouchService Implementation
// -------------------------------------------------------------

BleTouchService& BleTouchService::GetInstance() {
    static BleTouchService instance;
    return instance;
}

BleTouchService::BleTouchService() {
    touch_queue_ = xQueueCreate(64, sizeof(BleTouchEvent));
    snap_sem_ = xSemaphoreCreateBinary();
    ble_tx_mutex_ = xSemaphoreCreateMutex();

    // 800ms Watchdog timer to prevent runaway servos
    watchdog_timer_ = xTimerCreate("ble_wdt", pdMS_TO_TICKS(800), pdFALSE, this, WatchdogTimerCb);
}

BleTouchService::~BleTouchService() {
    Stop();
}

void BleTouchService::WatchdogTimerCb(TimerHandle_t timer) {
    auto svc = static_cast<BleTouchService*>(pvTimerGetTimerID(timer));
    if (svc && svc->motion_) {
        ESP_LOGW(TAG, "Watchdog expired! Safety emergency stop.");
        svc->motion_->Stop();
    }
}

void BleTouchService::FeedWatchdog() {
    if (watchdog_timer_) {
        xTimerReset(watchdog_timer_, 0);
    }
}

void BleTouchService::OnConnected(uint16_t conn_handle) {
    conn_handle_ = conn_handle;
    tx_val_handle_ = g_tx_val_handle;
    is_connected_ = true;
    mtu_ = 512;
    FeedWatchdog();
}

void BleTouchService::OnDisconnected() {
    is_connected_ = false;
    is_transferring_image_ = false;
    conn_handle_ = 0;
    if (motion_) {
        motion_->Stop();
    }
    // Release any stuck touches
    if (current_state_ == LV_INDEV_STATE_PRESSED) {
        current_state_ = LV_INDEV_STATE_RELEASED;
    }
}

void BleTouchService::OnMtuUpdated(uint16_t mtu) {
    mtu_ = mtu;
}

esp_err_t BleTouchService::SendNotifyPacket(const uint8_t* data, size_t len) {
    if (!is_connected_ || tx_val_handle_ == 0) {
        return ESP_ERR_INVALID_STATE;
    }

    if (ble_tx_mutex_) {
        xSemaphoreTake(ble_tx_mutex_, portMAX_DELAY);
    }

    esp_err_t ret = ESP_FAIL;
    // Retry up to 10 times if BLE controller buffers are temporarily congested
    for (int retry = 0; retry < 10 && is_connected_; ++retry) {
        struct os_mbuf *om = ble_hs_mbuf_from_flat(data, len);
        if (!om) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        int rc = ble_gatts_notify_custom(conn_handle_, tx_val_handle_, om);
        if (rc == 0) {
            ret = ESP_OK;
            break;
        }
        // If rc != 0 (e.g. BLE_HS_ENOMEM), wait for controller buffer to flush
        vTaskDelay(pdMS_TO_TICKS(15));
    }

    if (ble_tx_mutex_) {
        xSemaphoreGive(ble_tx_mutex_);
    }
    return ret;
}

void BleTouchService::SendPong(uint32_t timestamp) {
    uint8_t packet[5];
    packet[0] = BLE_MSG_PONG;
    packet[1] = (timestamp >> 24) & 0xFF;
    packet[2] = (timestamp >> 16) & 0xFF;
    packet[3] = (timestamp >> 8) & 0xFF;
    packet[4] = timestamp & 0xFF;
    SendNotifyPacket(packet, sizeof(packet));
}

void BleTouchService::OnReceiveRxData(const uint8_t* data, size_t len) {
    if (len < 1) return;
    FeedWatchdog();

    uint8_t type = data[0];
    switch (type) {
    case BLE_MSG_TOUCH: {
        // [type(1), action(1), x_hi(1), x_lo(1), y_hi(1), y_lo(1)] -> 6 bytes
        if (len >= 6) {
            BleTouchEvent ev;
            ev.action = static_cast<BleTouchAction>(data[1]);
            ev.x = (static_cast<int16_t>(data[2]) << 8) | data[3];
            ev.y = (static_cast<int16_t>(data[4]) << 8) | data[5];

            // Clamp coordinates to LCD bounds
            ev.x = std::clamp<int16_t>(ev.x, 0, 319);
            ev.y = std::clamp<int16_t>(ev.y, 0, 239);

            ESP_LOGI(TAG, "Virtual touch: act=%d (%s), x=%d, y=%d",
                     ev.action,
                     ev.action == 0 ? "DOWN" : (ev.action == 1 ? "MOVE" : "UP"),
                     ev.x, ev.y);

            if (touch_queue_) {
                xQueueSend(touch_queue_, &ev, 0);
            }
        }
        break;
    }

    case BLE_MSG_SERVO: {
        // [type(1), left(1 signed), right(1 signed), dur_hi(1), dur_lo(1)] -> 5 bytes
        if (len >= 5 && motion_) {
            int8_t left = static_cast<int8_t>(data[1]);
            int8_t right = static_cast<int8_t>(data[2]);
            uint16_t duration_ms = (static_cast<uint16_t>(data[3]) << 8) | data[4];
            ESP_LOGI(TAG, "Virtual drive: left=%d, right=%d, dur=%d ms", left, right, duration_ms);

            if (duration_ms > 0) {
                motion_->DriveForDuration(left, right, duration_ms);
            } else {
                motion_->SetWheelSpeeds(left, right);
            }
        }
        break;
    }

    case BLE_MSG_HEARTBEAT: {
        // [type(1), ts_3(1), ts_2(1), ts_1(1), ts_0(1)]
        if (len >= 5) {
            uint32_t ts = (static_cast<uint32_t>(data[1]) << 24) |
                          (static_cast<uint32_t>(data[2]) << 16) |
                          (static_cast<uint32_t>(data[3]) << 8)  |
                          static_cast<uint32_t>(data[4]);
            SendPong(ts);
        }
        break;
    }

    case BLE_MSG_SNAP_REQ: {
        // [type(1), source(1), optional quality(1)] -> 0=Screen, 1=Camera
        if (len >= 2) {
            snap_source_ = data[1];
            if (len >= 3 && data[2] >= 20 && data[2] <= 80) {
                snap_quality_ = data[2];
            }
            if (snap_sem_) {
                xSemaphoreGive(snap_sem_);
            }
        }
        break;
    }

    default:
        ESP_LOGW(TAG, "Unknown BLE Msg Type: 0x%02X", type);
        break;
    }
}

// -------------------------------------------------------------
// LVGL Virtual Touch InDev
// -------------------------------------------------------------

void BleTouchService::LvglTouchReadCallback(lv_indev_t* indev, lv_indev_data_t* data) {
    auto svc = static_cast<BleTouchService*>(lv_indev_get_user_data(indev));
    if (!svc) return;

    BleTouchEvent ev;
    if (xQueueReceive(svc->touch_queue_, &ev, 0) == pdTRUE) {
        svc->current_x_ = ev.x;
        svc->current_y_ = ev.y;
        if (ev.action == BLE_TOUCH_DOWN || ev.action == BLE_TOUCH_MOVE) {
            svc->current_state_ = LV_INDEV_STATE_PRESSED;
            svc->last_down_tick_ = xTaskGetTickCount();
        } else {
            svc->current_state_ = LV_INDEV_STATE_RELEASED;
        }

        // If more items in queue, continue processing
        if (uxQueueMessagesWaiting(svc->touch_queue_) > 0) {
            data->continue_reading = true;
        }
    } else {
        // Stuck touch auto-healing: if stuck pressed for > 2 seconds, auto release
        if (svc->current_state_ == LV_INDEV_STATE_PRESSED &&
            (xTaskGetTickCount() - svc->last_down_tick_) > pdMS_TO_TICKS(2000)) {
            svc->current_state_ = LV_INDEV_STATE_RELEASED;
        }
    }

    data->point.x = svc->current_x_;
    data->point.y = svc->current_y_;
    data->state = svc->current_state_;
}

void BleTouchService::RegisterVirtualTouchIndev() {
    if (virtual_indev_) return;

    lvgl_port_lock(0);
    virtual_indev_ = lv_indev_create();
    if (!virtual_indev_) {
        lvgl_port_unlock();
        ESP_LOGE(TAG, "Failed to create LVGL virtual indev");
        return;
    }
    lv_indev_set_type(virtual_indev_, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(virtual_indev_, LvglTouchReadCallback);
    lv_indev_set_user_data(virtual_indev_, this);
    lv_indev_set_disp(virtual_indev_, lv_display_get_default());
    lvgl_port_unlock();
    ESP_LOGI(TAG, "LVGL virtual touch indev registered successfully");
}

// -------------------------------------------------------------
// Background Tasks: Telemetry & Image Transfer
// -------------------------------------------------------------

void BleTouchService::TelemetryTask(void* param) {
    auto svc = static_cast<BleTouchService*>(param);
    TickType_t last_wake = xTaskGetTickCount();

    while (true) {
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(100)); // 10Hz

        if (!svc->is_connected_) continue;

        // Skip telemetry notifications during active image transfer to guarantee 100% bandwidth to image chunks
        if (svc->is_transferring_image_) continue;

        // Telemetry frame format: 25 bytes
        // [0]       Type = 0x81
        // [1..2]    Accel X (int16)
        // [3..4]    Accel Y (int16)
        // [5..6]    Accel Z (int16)
        // [7..8]    Gyro X (int16)
        // [9..10]   Gyro Y (int16)
        // [11..12]  Gyro Z (int16)
        // [13..14]  Pitch * 10 (int16)
        // [15..16]  Roll * 10 (int16)
        // [17]      Left Speed (int8)
        // [18]      Right Speed (int8)
        // [19..20]  Left Pulse Us (uint16)
        // [21..22]  Right Pulse Us (uint16)
        // [23..24]  CRC16 (uint16)

        uint8_t pkt[25] = {0};
        pkt[0] = BLE_MSG_TELEMETRY;

        if (svc->imu_) {
            ImuData imu_data;
            if (svc->imu_->ReadData(imu_data) == ESP_OK) {
                int16_t ax = (int16_t)(imu_data.ax * 1000.0f);
                int16_t ay = (int16_t)(imu_data.ay * 1000.0f);
                int16_t az = (int16_t)(imu_data.az * 1000.0f);
                int16_t gx = (int16_t)(imu_data.gx * 10.0f);
                int16_t gy = (int16_t)(imu_data.gy * 10.0f);
                int16_t gz = (int16_t)(imu_data.gz * 10.0f);
                int16_t pitch = (int16_t)(imu_data.pitch * 10.0f);
                int16_t roll = (int16_t)(imu_data.roll * 10.0f);

                pkt[1] = (ax >> 8) & 0xFF;  pkt[2] = ax & 0xFF;
                pkt[3] = (ay >> 8) & 0xFF;  pkt[4] = ay & 0xFF;
                pkt[5] = (az >> 8) & 0xFF;  pkt[6] = az & 0xFF;
                pkt[7] = (gx >> 8) & 0xFF;  pkt[8] = gx & 0xFF;
                pkt[9] = (gy >> 8) & 0xFF;  pkt[10] = gy & 0xFF;
                pkt[11] = (gz >> 8) & 0xFF; pkt[12] = gz & 0xFF;
                pkt[13] = (pitch >> 8) & 0xFF; pkt[14] = pitch & 0xFF;
                pkt[15] = (roll >> 8) & 0xFF;  pkt[16] = roll & 0xFF;
            }
        }

        if (svc->motion_) {
            pkt[17] = static_cast<uint8_t>(svc->motion_->GetLeftSpeed());
            pkt[18] = static_cast<uint8_t>(svc->motion_->GetRightSpeed());
            uint16_t pl = svc->motion_->GetLeftPulseUs();
            uint16_t pr = svc->motion_->GetRightPulseUs();
            pkt[19] = (pl >> 8) & 0xFF; pkt[20] = pl & 0xFF;
            pkt[21] = (pr >> 8) & 0xFF; pkt[22] = pr & 0xFF;
        }

        uint16_t crc = CalcCRC16(pkt, 23);
        pkt[23] = (crc >> 8) & 0xFF;
        pkt[24] = crc & 0xFF;

        svc->SendNotifyPacket(pkt, sizeof(pkt));
    }
}

void BleTouchService::ImageTask(void* param) {
    auto svc = static_cast<BleTouchService*>(param);

    while (true) {
        if (xSemaphoreTake(svc->snap_sem_, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (!svc->is_connected_) continue;

        svc->frame_seq_++;
        uint16_t frame_id = svc->frame_seq_;

        uint8_t* jpeg_buf = nullptr;
        size_t jpeg_len = 0;
        bool free_jpeg = false;

        if (svc->snap_source_ == 0) {
            // Screen snapshot
#if LV_USE_SNAPSHOT
            lvgl_port_lock(0);
            lv_draw_buf_t* dsc = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB565);
            lvgl_port_unlock();

            if (dsc && dsc->data) {
                uint32_t w = (dsc->header.w > 0) ? dsc->header.w : 320;
                uint32_t h = (dsc->header.h > 0) ? dsc->header.h : 240;
                uint8_t q = (svc->snap_quality_ >= 20 && svc->snap_quality_ <= 80) ? svc->snap_quality_ : 32;
                image_to_jpeg((uint8_t*)dsc->data, dsc->data_size, w, h,
                              V4L2_PIX_FMT_RGB565, q, &jpeg_buf, &jpeg_len);
                free_jpeg = true;
                lv_draw_buf_destroy(dsc);
            }
#endif
        } else if (svc->snap_source_ == 1 && svc->camera_) {
            // Camera snapshot
            if (svc->camera_->Capture()) {
                auto fb = svc->camera_->GetCurrentFrameBuffer();
                if (fb && fb->buf && fb->len > 0) {
                    if (fb->format == PIXFORMAT_JPEG) {
                        jpeg_buf = fb->buf;
                        jpeg_len = fb->len;
                        free_jpeg = false;
                    } else if (fb->format == PIXFORMAT_RGB565) {
                        image_to_jpeg(fb->buf, fb->len, fb->width, fb->height,
                                      V4L2_PIX_FMT_RGB565, 55, &jpeg_buf, &jpeg_len);
                        free_jpeg = true;
                    }
                }
            }
        }

        if (!jpeg_buf || jpeg_len == 0) {
            ESP_LOGW(TAG, "Failed capture (source=%d), falling back to screen", svc->snap_source_);
#if LV_USE_SNAPSHOT
            lvgl_port_lock(0);
            lv_draw_buf_t* dsc = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB565);
            lvgl_port_unlock();

            if (dsc && dsc->data) {
                uint32_t w = (dsc->header.w > 0) ? dsc->header.w : 320;
                uint32_t h = (dsc->header.h > 0) ? dsc->header.h : 240;
                uint8_t q = (svc->snap_quality_ >= 20 && svc->snap_quality_ <= 80) ? svc->snap_quality_ : 32;
                image_to_jpeg((uint8_t*)dsc->data, dsc->data_size, w, h,
                              V4L2_PIX_FMT_RGB565, q, &jpeg_buf, &jpeg_len);
                free_jpeg = true;
                lv_draw_buf_destroy(dsc);
            }
#endif
            if (!jpeg_buf || jpeg_len == 0) {
                continue;
            }
        }

        svc->is_transferring_image_ = true;

        ESP_LOGI(TAG, "Transferring JPEG: %d bytes (frame_id=%d, q=%d)",
                 (int)jpeg_len, frame_id, svc->snap_quality_);

        // 220-byte payload reduces total chunk count to ~10-12 chunks for high frame rate
        const size_t kMaxChunkPayload = 220;
        uint16_t total_chunks = (jpeg_len + kMaxChunkPayload - 1) / kMaxChunkPayload;

        // Packet Header: 11 bytes
        // [0]       Type = 0x82
        // [1..2]    frame_id
        // [3..4]    total_chunks
        // [5..6]    chunk_idx (0-based)
        // [7..8]    chunk_len
        // [9..N-3]  payload
        // [N-2..N-1] CRC16 of chunk

        uint8_t chunk_pkt[kMaxChunkPayload + 12];
        chunk_pkt[0] = BLE_MSG_IMAGE_CHUNK;
        chunk_pkt[1] = (frame_id >> 8) & 0xFF;
        chunk_pkt[2] = frame_id & 0xFF;
        chunk_pkt[3] = (total_chunks >> 8) & 0xFF;
        chunk_pkt[4] = total_chunks & 0xFF;

        for (uint16_t idx = 0; idx < total_chunks; ++idx) {
            if (!svc->is_connected_) break;

            size_t offset = idx * kMaxChunkPayload;
            size_t this_len = std::min(kMaxChunkPayload, jpeg_len - offset);

            chunk_pkt[5] = (idx >> 8) & 0xFF;
            chunk_pkt[6] = idx & 0xFF;
            chunk_pkt[7] = (this_len >> 8) & 0xFF;
            chunk_pkt[8] = this_len & 0xFF;

            memcpy(&chunk_pkt[9], &jpeg_buf[offset], this_len);

            uint16_t crc = CalcCRC16(chunk_pkt, 9 + this_len);
            chunk_pkt[9 + this_len] = (crc >> 8) & 0xFF;
            chunk_pkt[10 + this_len] = crc & 0xFF;

            esp_err_t err = svc->SendNotifyPacket(chunk_pkt, 11 + this_len);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "Failed to send chunk %d/%d, retrying once...", idx, total_chunks);
                vTaskDelay(1);
                svc->SendNotifyPacket(chunk_pkt, 11 + this_len);
            }

            // Paced transmission: 1 FreeRTOS tick (10ms) per chunk
            vTaskDelay(1);
        }

        if (free_jpeg && jpeg_buf) {
            free(jpeg_buf);
        }
        svc->is_transferring_image_ = false;
        ESP_LOGI(TAG, "JPEG transfer finished (frame_id=%d, chunks=%d)", frame_id, total_chunks);
    }
}

// -------------------------------------------------------------
// NimBLE Host & Service Setup
// -------------------------------------------------------------

void BleTouchService::NimbleHostTask(void* param) {
    ESP_LOGI(TAG, "NimBLE host task started");
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void BleTouchService::InitNimble() {
    esp_err_t ret = nimble_port_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init failed: %d", ret);
        return;
    }

    ble_hs_cfg.sync_cb = OnSync;
    ble_hs_cfg.reset_cb = OnReset;

    ble_svc_gap_init();
    ble_svc_gatt_init();

    int rc = ble_gatts_count_cfg(g_gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gatts_count_cfg failed: %d", rc);
        return;
    }

    rc = ble_gatts_add_svcs(g_gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gatts_add_svcs failed: %d", rc);
        return;
    }

    ble_svc_gap_device_name_set(DEVICE_NAME);
    nimble_port_freertos_init(NimbleHostTask);
}

esp_err_t BleTouchService::Start(MotionController* motion, Qmi8658* imu, Esp32Camera* camera) {
    motion_ = motion;
    imu_ = imu;
    camera_ = camera;

    InitNimble();

    xTaskCreate(TelemetryTask, "ble_telemetry", 4096, this, 3, &telemetry_task_);
    xTaskCreate(ImageTask, "ble_image", 4096, this, 4, &image_task_);

    ESP_LOGI(TAG, "BleTouchService initialized and running");
    return ESP_OK;
}

void BleTouchService::Stop() {
    if (watchdog_timer_) {
        xTimerStop(watchdog_timer_, 0);
    }
    if (telemetry_task_) {
        vTaskDelete(telemetry_task_);
        telemetry_task_ = nullptr;
    }
    if (image_task_) {
        vTaskDelete(image_task_);
        image_task_ = nullptr;
    }
}
