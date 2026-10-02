#include "wifi_board.h"
#include "codecs/box_audio_codec.h"
#include "display/lcd_display.h"
#include "display/emote_display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "i2c_device.h"
#include "esp32_camera.h"
#include "mcp_server.h"
#include "press_to_talk_mcp_tool.h"
#include "qmi8658.h"
#if CONFIG_DESKBOT_MOTION_PCA9685
#include "dual_wheel_controller.h"
#include "servo_control_ui.h"
#endif

#include <esp_log.h>
#include <esp_lcd_panel_vendor.h>
#include <driver/i2c_master.h>
#include <driver/spi_common.h>
#include <esp_lcd_touch_ft5x06.h>
#include <esp_lvgl_port.h>
#include <lvgl.h>
#include <memory>
#include <mbedtls/base64.h>
#include <soc/lcd_cam_struct.h>
#include <esp_timer.h>

extern "C" {
void cam_set_psram_mode(bool enable);
bool cam_get_psram_mode(void);
}

#define TAG "LichuangDevBoard"

class Pca9557 : public I2cDevice {
public:
    Pca9557(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : I2cDevice(i2c_bus, addr) {
        WriteReg(0x01, 0x03);
        WriteReg(0x03, 0xf8);
    }

    void SetOutputState(uint8_t bit, uint8_t level) {
        uint8_t data = ReadReg(0x01);
        data = (data & ~(1 << bit)) | (level << bit);
        WriteReg(0x01, data);
    }
};

class CustomAudioCodec : public BoxAudioCodec {
private:
    Pca9557* pca9557_;

public:
    CustomAudioCodec(i2c_master_bus_handle_t i2c_bus, Pca9557* pca9557)
        : BoxAudioCodec(i2c_bus, AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
                        AUDIO_I2S_GPIO_MCLK, AUDIO_I2S_GPIO_BCLK, AUDIO_I2S_GPIO_WS,
                        AUDIO_I2S_GPIO_DOUT, AUDIO_I2S_GPIO_DIN, GPIO_NUM_NC,
                        AUDIO_CODEC_ES8311_ADDR, AUDIO_CODEC_ES7210_ADDR, AUDIO_INPUT_REFERENCE,
                        28.0f,  // Physical MIC1 gain
                        2,      // Physical MIC3 is the playback reference input
                        0.0f),
          pca9557_(pca9557) {}

    virtual void EnableOutput(bool enable) override {
        BoxAudioCodec::EnableOutput(enable);
        if (enable) {
            pca9557_->SetOutputState(1, 1);
        } else {
            pca9557_->SetOutputState(1, 0);
        }
    }
};

#if CONFIG_DESKBOT_MOTION_PCA9685
class LichuangLcdDisplay : public SpiLcdDisplay {
private:
    DualWheelController* motion_controller_ = nullptr;
    Qmi8658* imu_ = nullptr;
    Esp32Camera* camera_ = nullptr;
    std::unique_ptr<ServoControlUi> servo_ui_;

public:
    LichuangLcdDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
                       int width, int height, int offset_x, int offset_y, bool mirror_x,
                       bool mirror_y, bool swap_xy, DualWheelController* motion,
                       Qmi8658* imu, Esp32Camera* camera)
        : SpiLcdDisplay(panel_io, panel, width, height, offset_x, offset_y, mirror_x, mirror_y, swap_xy),
          motion_controller_(motion), imu_(imu), camera_(camera) {}

    virtual void SetupUI() override {
        DisplayLockGuard lock(this);
        SpiLcdDisplay::SetupUI();
        servo_ui_ = std::make_unique<ServoControlUi>(motion_controller_, imu_, camera_);
        servo_ui_->SetupUI();
    }

    virtual void SetPreviewImage(std::unique_ptr<LvglImage> image) override {
        DisplayLockGuard lock(this);
        if (servo_ui_ && servo_ui_->IsCamPanelVisible()) {
            return;
        }
        SpiLcdDisplay::SetPreviewImage(std::move(image));
    }

    void SetCamera(Esp32Camera* camera) {
        camera_ = camera;
        if (servo_ui_) servo_ui_->SetCamera(camera);
    }
    void SetImu(Qmi8658* imu) {
        imu_ = imu;
        if (servo_ui_) servo_ui_->SetImu(imu);
    }
    void SetMotion(DualWheelController* motion) {
        motion_controller_ = motion;
        if (servo_ui_) servo_ui_->SetMotion(motion);
    }
};
#endif

class LichuangDevBoard : public WifiBoard {
private:
    i2c_master_bus_handle_t i2c_bus_ = nullptr;
    i2c_master_dev_handle_t pca9557_handle_ = nullptr;
    Button boot_button_;
    Display* display_ = nullptr;
    Pca9557* pca9557_ = nullptr;
    Esp32Camera* camera_ = nullptr;
    PressToTalkMcpTool* press_to_talk_tool_ = nullptr;
    std::unique_ptr<Qmi8658> imu_;
#if CONFIG_DESKBOT_MOTION_PCA9685
    std::unique_ptr<DualWheelController> motion_controller_;
#endif

    void InitializeI2c() {
        // Initialize I2C peripheral
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = (i2c_port_t)1,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &i2c_bus_));

        // Initialize PCA9557
        pca9557_ = new Pca9557(i2c_bus_, 0x19);
    }

    void InitializeImu() {
        if (Qmi8658::Probe(i2c_bus_, Qmi8658::kDefaultAddress) == ESP_OK) {
            imu_ = std::make_unique<Qmi8658>(i2c_bus_, Qmi8658::kDefaultAddress);
        } else if (Qmi8658::Probe(i2c_bus_, Qmi8658::kAltAddress) == ESP_OK) {
            imu_ = std::make_unique<Qmi8658>(i2c_bus_, Qmi8658::kAltAddress);
        }
        if (imu_ != nullptr && imu_->Initialize() != ESP_OK) {
            imu_.reset();
            ESP_LOGW(TAG, "QMI8658 probed but failed to initialize");
        }
    }

    void InitializeSpi() {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = GPIO_NUM_40;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = GPIO_NUM_41;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            // During startup (before connected), pressing BOOT button enters Wi-Fi config mode without reboot
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            // In press-to-talk mode the click event is handled by press down/up
            if (!press_to_talk_tool_ || !press_to_talk_tool_->IsPressToTalkEnabled()) {
                app.ToggleChatState();
            }
        });

        boot_button_.OnPressDown([this]() {
            if (press_to_talk_tool_ && press_to_talk_tool_->IsPressToTalkEnabled()) {
                Application::GetInstance().StartListening();
            }
        });
        boot_button_.OnPressUp([this]() {
            if (press_to_talk_tool_ && press_to_talk_tool_->IsPressToTalkEnabled()) {
                Application::GetInstance().StopListening();
            }
        });

#if CONFIG_USE_DEVICE_AEC
        boot_button_.OnDoubleClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateIdle) {
                app.SetAecMode(app.GetAecMode() == kAecOff ? kAecOnDeviceSide : kAecOff);
            }
        });
#endif
    }

    void InitializeSt7789Display() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;
        // 液晶屏控制IO初始化
        ESP_LOGD(TAG, "Install panel IO");
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = GPIO_NUM_NC;
        io_config.dc_gpio_num = GPIO_NUM_39;
        io_config.spi_mode = 2;
        io_config.pclk_hz = 80 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        // 初始化液晶屏驱动芯片ST7789
        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = GPIO_NUM_NC;
        panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
        panel_config.bits_per_pixel = 16;
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel));
        
        esp_lcd_panel_reset(panel);
        pca9557_->SetOutputState(0, 0);

        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, true);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY);
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
        esp_lcd_panel_disp_on_off(panel, true);

#if CONFIG_USE_EMOTE_MESSAGE_STYLE
        display_ = new emote::EmoteDisplay(panel, panel_io, DISPLAY_WIDTH, DISPLAY_HEIGHT);
#elif CONFIG_DESKBOT_MOTION_PCA9685
        display_ = new LichuangLcdDisplay(panel_io, panel,
            DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY,
            motion_controller_.get(), imu_.get(), camera_);
#else
        display_ = new SpiLcdDisplay(panel_io, panel,
            DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
#endif
    }

    void InitializeTouch()
    {
        esp_lcd_touch_handle_t tp = nullptr;
        esp_lcd_touch_config_t tp_cfg = {
            .x_max = DISPLAY_HEIGHT,
            .y_max = DISPLAY_WIDTH,
            .rst_gpio_num = GPIO_NUM_NC, // Shared with LCD reset
            .int_gpio_num = GPIO_NUM_NC, 
            .levels = {
                .reset = 0,
                .interrupt = 0,
            },
            .flags = {
                .swap_xy = 1,
                .mirror_x = 1,
                .mirror_y = 0,
            },
        };
        esp_lcd_panel_io_handle_t tp_io_handle = NULL;
        esp_lcd_panel_io_i2c_config_t tp_io_config = {
            .dev_addr = ESP_LCD_TOUCH_IO_I2C_FT5x06_ADDRESS,
            .control_phase_bytes = 1,
            .dc_bit_offset = 0,
            .lcd_cmd_bits = 8,
            .flags =
            {
                .disable_control_phase = 1,
            }
        };
        tp_io_config.scl_speed_hz = 400000;

        if (esp_lcd_new_panel_io_i2c(i2c_bus_, &tp_io_config, &tp_io_handle) != ESP_OK) {
            ESP_LOGW(TAG, "Failed to create touch panel IO, continuing without touch");
            return;
        }
        if (esp_lcd_touch_new_i2c_ft5x06(tp_io_handle, &tp_cfg, &tp) != ESP_OK || tp == nullptr) {
            ESP_LOGW(TAG, "FT5x06 touch controller not found, continuing without touch");
            esp_lcd_panel_io_del(tp_io_handle);
            return;
        }

        /* Add touch input (for selected screen) */
        const lvgl_port_touch_cfg_t touch_cfg = {
            .disp = lv_display_get_default(), 
            .handle = tp,
        };

        if(touch_cfg.disp) {
            lvgl_port_add_touch(&touch_cfg);
        } else {
            ESP_LOGE(TAG, "Touch display is not initialized");
        }
    }

    void InitializeCamera() {
        // Open camera power
        pca9557_->SetOutputState(2, 0);

        // Turn off direct PSRAM DMA: use internal SRAM ping-pong DMA buffers
        // to prevent external PSRAM bus contention and descriptor-boundary byte drops
        cam_set_psram_mode(false);

        camera_config_t config = {};
        config.ledc_channel = LEDC_CHANNEL_2;
        config.ledc_timer = LEDC_TIMER_2;
        config.pin_d0 = CAMERA_PIN_D0;
        config.pin_d1 = CAMERA_PIN_D1;
        config.pin_d2 = CAMERA_PIN_D2;
        config.pin_d3 = CAMERA_PIN_D3;
        config.pin_d4 = CAMERA_PIN_D4;
        config.pin_d5 = CAMERA_PIN_D5;
        config.pin_d6 = CAMERA_PIN_D6;
        config.pin_d7 = CAMERA_PIN_D7;
        config.pin_xclk = CAMERA_PIN_XCLK;
        config.pin_pclk = CAMERA_PIN_PCLK;
        config.pin_vsync = CAMERA_PIN_VSYNC;
        config.pin_href = CAMERA_PIN_HREF;
        config.pin_sccb_sda = -1;
        config.pin_sccb_scl = CAMERA_PIN_SIOC;
        config.sccb_i2c_port = 1;
        config.pin_pwdn = CAMERA_PIN_PWDN;
        config.pin_reset = CAMERA_PIN_RESET;
        config.xclk_freq_hz = XCLK_FREQ_HZ;
        config.pixel_format = PIXFORMAT_RGB565;
        config.frame_size = FRAMESIZE_QVGA;
        config.jpeg_quality = 12;
        config.fb_count = 1;
        config.fb_location = CAMERA_FB_IN_PSRAM;
        config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

        auto* cam = new Esp32Camera(config);

        // Invert PCLK sampling edge to match sensor data eye
        LCD_CAM.cam_ctrl1.cam_clk_inv = 1;
        LCD_CAM.cam_ctrl.cam_update = 1;

        camera_ = cam;
    }

#if CONFIG_DESKBOT_MOTION_PCA9685
    void InitializeMotion() {
        motion_controller_ = DualWheelController::Create(i2c_bus_);
        if (motion_controller_ == nullptr || !motion_controller_->Initialize()) {
            motion_controller_.reset();
            ESP_LOGW(TAG, "PCA9685 motion disabled because the controller is unavailable");
        }
    }
#endif

    void InitializeTools() {
        auto &mcp_server = McpServer::GetInstance();
        mcp_server.AddTool("self.system.reconfigure_wifi",
            "End this conversation and enter WiFi configuration mode.\n"
            "**CAUTION** You must ask the user to confirm this action.",
            PropertyList(), [this](const PropertyList& properties) {
                EnterWifiConfigMode();
                return true;
            });

        // Allow switching between press-to-talk (长按说话) and click-to-talk (单击唤醒)
        press_to_talk_tool_ = new PressToTalkMcpTool();
        press_to_talk_tool_->Initialize();
    }

    void RegisterDeferredTools() {
        auto &mcp_server = McpServer::GetInstance();
#if CONFIG_DESKBOT_MOTION_PCA9685
        if (motion_controller_) {
            motion_controller_->RegisterMcpTools(mcp_server);
        }
#endif
        if (imu_) {
            mcp_server.AddTool("self.sensor.get_imu", "Read live 6-axis IMU (accel in g, gyro in dps, tilt pitch/roll)",
                PropertyList(), [this](const PropertyList&) -> ToolResult {
                    ImuData d;
                    if (imu_->ReadData(d) != ESP_OK) {
                        return std::unexpected("Failed to read IMU data");
                    }
                    char buf[128];
                    snprintf(buf, sizeof(buf),
                        "{\"ax\": %.2f, \"ay\": %.2f, \"az\": %.2f, \"gx\": %.1f, \"gy\": %.1f, \"gz\": %.1f, \"pitch\": %.1f, \"roll\": %.1f}",
                        d.ax, d.ay, d.az, d.gx, d.gy, d.gz, d.pitch, d.roll);
                    return std::string(buf);
                });
        }
    }

    void DumpCameraFrame() {
        if (!camera_) {
            printf("ERR:NO_CAM\n");
            fflush(stdout);
            return;
        }
        sensor_t* s = esp_camera_sensor_get();
        if (s) {
            printf("CAM_INFO:PID=0x%04x,SLV=0x%02x,CLK_INV=%d,PSRAM_DMA=%d\n",
                   s->id.PID, s->slv_addr, (int)LCD_CAM.cam_ctrl1.cam_clk_inv, (int)cam_get_psram_mode());
            fflush(stdout);
        }
        if (!camera_->Capture()) {
            printf("ERR:CAP_FAIL\n");
            fflush(stdout);
            return;
        }
        camera_fb_t* fb = camera_->GetCurrentFrameBuffer();
        if (!fb || !fb->buf) {
            printf("ERR:NULL_BUF\n");
            fflush(stdout);
            return;
        }
        printf("===BEGIN_FRAME:W=%d,H=%d,LEN=%zu===\n", fb->width, fb->height, fb->len);
        fflush(stdout);
        const uint8_t* p = fb->buf;
        size_t rem = fb->len;
        unsigned char b64_out[2048];
        while (rem > 0) {
            size_t chunk = rem > 1024 ? 1024 : rem;
            size_t olen = 0;
            mbedtls_base64_encode(b64_out, sizeof(b64_out), &olen, p, chunk);
            b64_out[olen] = '\0';
            printf("%s\n", (char*)b64_out);
            p += chunk;
            rem -= chunk;
        }
        printf("===END_FRAME===\n");
        fflush(stdout);
    }

    void StartSerialDiagnosticTask() {
        xTaskCreate([](void* arg) {
            auto* b = static_cast<LichuangDevBoard*>(arg);
            char line[64];
            while (true) {
                if (fgets(line, sizeof(line), stdin) != nullptr) {
                    if (strncmp(line, "DUMP_FRAME", 10) == 0) {
                        b->DumpCameraFrame();
                    } else if (strncmp(line, "SET_CLK_INV ", 12) == 0) {
                        int v = atoi(line + 12);
                        LCD_CAM.cam_ctrl1.cam_clk_inv = v ? 1 : 0;
                        LCD_CAM.cam_ctrl.cam_update = 1;
                        printf("ACK:CLK_INV=%d\n", (int)LCD_CAM.cam_ctrl1.cam_clk_inv);
                        fflush(stdout);
                    } else if (strncmp(line, "SET_PSRAM_DMA ", 14) == 0) {
                        int v = atoi(line + 14);
                        cam_set_psram_mode(v != 0);
                        printf("ACK:PSRAM_DMA=%d\n", (int)cam_get_psram_mode());
                        fflush(stdout);
                    }
                }
                vTaskDelay(pdMS_TO_TICKS(50));
            }
        }, "serial_diag", 4096, this, 3, nullptr);
    }

    void StartDeferredInitializationTask() {
        xTaskCreate([](void* arg) {
            auto* b = static_cast<LichuangDevBoard*>(arg);
            // Wait 3.0s until Wi-Fi association and main UI are fully stable
            vTaskDelay(pdMS_TO_TICKS(3000));
            ESP_LOGI(TAG, "Starting deferred peripheral initialization (battery-safe schedule)...");

            // 1. Initialize QMI8658 IMU
            b->InitializeImu();
            if (b->imu_) {
#if CONFIG_DESKBOT_MOTION_PCA9685
                auto* disp = static_cast<LichuangLcdDisplay*>(b->display_);
                if (disp) disp->SetImu(b->imu_.get());
#endif
                ESP_LOGI(TAG, "Deferred: QMI8658 IMU attached");
            }
            vTaskDelay(pdMS_TO_TICKS(60));

            // 2. Initialize PCA9685 Motion Controller (if present)
#if CONFIG_DESKBOT_MOTION_PCA9685
            b->InitializeMotion();
            if (b->motion_controller_) {
                auto* disp = static_cast<LichuangLcdDisplay*>(b->display_);
                if (disp) disp->SetMotion(b->motion_controller_.get());
                ESP_LOGI(TAG, "Deferred: PCA9685 motion attached");
            }
            vTaskDelay(pdMS_TO_TICKS(60));
#endif

            // 3. Power on and Initialize GC2145 Camera
            b->pca9557_->SetOutputState(2, 0); // Power on camera
            vTaskDelay(pdMS_TO_TICKS(80));
            b->InitializeCamera();
            if (b->camera_) {
#if CONFIG_DESKBOT_MOTION_PCA9685
                auto* disp = static_cast<LichuangLcdDisplay*>(b->display_);
                if (disp) disp->SetCamera(b->camera_);
#endif
                ESP_LOGI(TAG, "Deferred: GC2145 camera attached");
            }
            vTaskDelay(pdMS_TO_TICKS(60));

            // 4. Register MCP tools and serial diagnostic task
            b->RegisterDeferredTools();
            b->StartSerialDiagnosticTask();

            // 5. Restore backlight to full user setting smoothly
            b->GetBacklight()->RestoreBrightness();

            ESP_LOGI(TAG, "Deferred peripheral initialization complete.");
            vTaskDelete(nullptr);
        }, "deferred_init", 4096, this, 2, nullptr);
    }

public:
    LichuangDevBoard() : boot_button_(BOOT_BUTTON_GPIO) {
        InitializeI2c();
        // Camera power initially forced OFF during boot to keep inrush current minimal (<120mA)
        pca9557_->SetOutputState(2, 1);

        InitializeSpi();
        InitializeSt7789Display();
        InitializeTouch();
        InitializeButtons();
        InitializeTools();

        // Moderate steady backlight (45%) on boot without blinking or inrush surge
        GetBacklight()->SetBrightness(45, false);

        // Start deferred peripheral initialization task (runs 3.0s after boot)
        StartDeferredInitializationTask();
    }

    virtual AudioCodec* GetAudioCodec() override {
        static CustomAudioCodec audio_codec(
            i2c_bus_, 
            pca9557_);
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }
    
    virtual Backlight* GetBacklight() override {
        static PwmBacklight backlight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        return &backlight;
    }

    virtual Camera* GetCamera() override {
        return camera_;
    }
};

DECLARE_BOARD(LichuangDevBoard);
