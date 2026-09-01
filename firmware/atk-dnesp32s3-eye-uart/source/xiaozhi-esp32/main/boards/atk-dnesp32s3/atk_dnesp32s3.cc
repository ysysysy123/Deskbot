#include "wifi_board.h"
#include "codecs/es8388_audio_codec.h"
#include "display/lcd_display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "i2c_device.h"
#include "led/single_led.h"
#include "esp32_camera.h"
#include "assets/lang_config.h"

#include <esp_log.h>
#include <esp_lcd_panel_vendor.h>
#include <driver/i2c_master.h>
#include <driver/spi_common.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/idf_additions.h>
#include <freertos/task.h>

#define TAG "atk_dnesp32s3"

class XL9555 : public I2cDevice {
public:
    XL9555(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : I2cDevice(i2c_bus, addr) {
        WriteReg(0x06, 0x03);
        WriteReg(0x07, 0xF0);
    }

    // 读取输入端口 1（寄存器 0x01）。KEY0~KEY3 位于 P1_7~P1_4（bit7~bit4），低电平有效。
    esp_err_t ReadInputPort1(uint8_t* value) {
        uint8_t reg = 0x01;
        return i2c_master_transmit_receive(i2c_device_, &reg, 1, value, 1, 20);
    }

    void SetOutputState(uint8_t bit, uint8_t level) {
        uint16_t data;
        int index = bit;

        if (bit < 8) {
            data = ReadReg(0x02);
        } else {
            data = ReadReg(0x03);
            index -= 8;
        }

        data = (data & ~(1 << index)) | (level << index);

        if (bit < 8) {
            WriteReg(0x02, data);
        } else {
            WriteReg(0x03, data);
        }
    }
};

class atk_dnesp32s3 : public WifiBoard {
private:
    i2c_master_bus_handle_t i2c_bus_;
    Button boot_button_;
    LcdDisplay* display_;
    XL9555* xl9555_;
    Esp32Camera* camera_;
    TaskHandle_t volume_key_task_ = nullptr;

    void InitializeI2c() {
        // Initialize I2C peripheral
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = (i2c_port_t)I2C_NUM_0,
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

        // Initialize XL9555
        xl9555_ = new XL9555(i2c_bus_, 0x20);
    }

    // Initialize spi peripheral
    void InitializeSpi() {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = LCD_MOSI_PIN;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = LCD_SCLK_PIN;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            app.ToggleChatState();
        });
    }

    void AdjustVolume(int delta) {
        // The key task uses a PSRAM stack. Run codec control and NVS volume
        // persistence on the application task, whose stack is in internal RAM.
        Application::GetInstance().Schedule([delta]() {
            auto codec = Board::GetInstance().GetAudioCodec();
            if (codec == nullptr) {
                return;
            }

            int volume = codec->output_volume() + delta;
            if (volume < 0) {
                volume = 0;
            } else if (volume > 100) {
                volume = 100;
            }
            ESP_LOGI(TAG, "Volume key: %+d -> %d", delta, volume);
            codec->SetOutputVolume(volume);

            auto display = Board::GetInstance().GetDisplay();
            std::string message;
            if (volume == 0) {
                message = Lang::Strings::MUTED;
            } else if (volume == 100) {
                message = Lang::Strings::MAX_VOLUME;
            } else {
                message = std::string(Lang::Strings::VOLUME) + std::to_string(volume);
            }
            display->ShowNotification(message, 1000);
            display->UpdateStatusBar(true);
        });
    }

    void InitializeVolumeKeys() {
        // KEY1 = P1_6（bit6）调小音量，KEY3 = P1_4（bit4）调大音量，均为低电平有效。
        BaseType_t task_created = xTaskCreateWithCaps([](void* arg) {
            auto* self = static_cast<atk_dnesp32s3*>(arg);
            constexpr uint8_t KEY1_BIT = 6;
            constexpr uint8_t KEY3_BIT = 4;
            constexpr int VOLUME_STEP = 5;
            constexpr TickType_t POLL_INTERVAL = pdMS_TO_TICKS(20);
            constexpr TickType_t LONG_PRESS_DELAY = pdMS_TO_TICKS(450);
            constexpr TickType_t REPEAT_INTERVAL = pdMS_TO_TICKS(120);

            uint8_t raw = 0xFF;
            uint32_t read_error_count = 0;
            while (self->xl9555_->ReadInputPort1(&raw) != ESP_OK) {
                vTaskDelay(pdMS_TO_TICKS(100));
            }
            uint8_t key1_state = (raw >> KEY1_BIT) & 1;
            uint8_t key3_state = (raw >> KEY3_BIT) & 1;
            uint8_t key1_count = 0;
            uint8_t key3_count = 0;
            TickType_t key1_pressed_at = 0;
            TickType_t key3_pressed_at = 0;
            TickType_t key1_last_repeat = 0;
            TickType_t key3_last_repeat = 0;
            TickType_t last_wake = xTaskGetTickCount();

            while (true) {
                vTaskDelayUntil(&last_wake, POLL_INTERVAL);
                esp_err_t ret = self->xl9555_->ReadInputPort1(&raw);
                if (ret != ESP_OK) {
                    if ((read_error_count++ % 50) == 0) {
                        ESP_LOGW(TAG, "Volume key I2C read failed: %s", esp_err_to_name(ret));
                    }
                    continue;
                }
                read_error_count = 0;

                uint8_t key1 = (raw >> KEY1_BIT) & 1;
                uint8_t key3 = (raw >> KEY3_BIT) & 1;
                TickType_t now = xTaskGetTickCount();

                key1_count = (key1 == key1_state) ? 0 : key1_count + 1;
                key3_count = (key3 == key3_state) ? 0 : key3_count + 1;

                if (key1_count >= 2) {
                    key1_state = key1;
                    key1_count = 0;
                    if (key1_state == 0) {
                        key1_pressed_at = now;
                        key1_last_repeat = now;
                        if (key3_state != 0) {
                            self->AdjustVolume(-VOLUME_STEP);
                        }
                    }
                }
                if (key3_count >= 2) {
                    key3_state = key3;
                    key3_count = 0;
                    if (key3_state == 0) {
                        key3_pressed_at = now;
                        key3_last_repeat = now;
                        if (key1_state != 0) {
                            self->AdjustVolume(VOLUME_STEP);
                        }
                    }
                }

                if (key1_state == 0 && key3_state != 0 &&
                    now - key1_pressed_at >= LONG_PRESS_DELAY &&
                    now - key1_last_repeat >= REPEAT_INTERVAL) {
                    key1_last_repeat = now;
                    self->AdjustVolume(-VOLUME_STEP);
                } else if (key3_state == 0 && key1_state != 0 &&
                           now - key3_pressed_at >= LONG_PRESS_DELAY &&
                           now - key3_last_repeat >= REPEAT_INTERVAL) {
                    key3_last_repeat = now;
                    self->AdjustVolume(VOLUME_STEP);
                }
            }
        }, "vol_keys", 4096, this, 5, &volume_key_task_, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (task_created != pdPASS) {
            ESP_LOGE(TAG, "Failed to create volume key task in PSRAM");
        }
    }

    void InitializeSt7789Display() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;
        ESP_LOGD(TAG, "Install panel IO");
        // 液晶屏控制IO初始化
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = LCD_CS_PIN;
        io_config.dc_gpio_num = LCD_DC_PIN;
        io_config.spi_mode = 0;
        io_config.pclk_hz = 20 * 1000 * 1000;
        io_config.trans_queue_depth = 7;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        esp_lcd_new_panel_io_spi(SPI2_HOST, &io_config, &panel_io);

        // 初始化液晶屏驱动芯片ST7789
        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = GPIO_NUM_NC;
        panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
        panel_config.bits_per_pixel = 16;
        panel_config.data_endian = LCD_RGB_DATA_ENDIAN_BIG,
        esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel);
        
        esp_lcd_panel_reset(panel);
        xl9555_->SetOutputState(8, 1);
        xl9555_->SetOutputState(2, 0);

        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY); 
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
        display_ = new SpiLcdDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
    }

    // 使用正点原子已验证的 esp32-camera + RGB565/QVGA 路径。
    // esp_video 的 OV5640 YUV/SVGA 路径在本板上会产生横向错位和绿色色块。
    void InitializeCamera() {
        xl9555_->SetOutputState(OV_PWDN_IO, 0); // PWDN=低 (上电)
        xl9555_->SetOutputState(OV_RESET_IO, 0); // 确保复位
        vTaskDelay(pdMS_TO_TICKS(50));           // 延长复位保持时间
        xl9555_->SetOutputState(OV_RESET_IO, 1); // 释放复位
        vTaskDelay(pdMS_TO_TICKS(50));           // 延长 50ms

        camera_config_t config = {};
        config.pin_d0 = CAM_PIN_D0;
        config.pin_d1 = CAM_PIN_D1;
        config.pin_d2 = CAM_PIN_D2;
        config.pin_d3 = CAM_PIN_D3;
        config.pin_d4 = CAM_PIN_D4;
        config.pin_d5 = CAM_PIN_D5;
        config.pin_d6 = CAM_PIN_D6;
        config.pin_d7 = CAM_PIN_D7;
        config.pin_xclk = CAM_PIN_XCLK;
        config.pin_pclk = CAM_PIN_PCLK;
        config.pin_vsync = CAM_PIN_VSYNC;
        config.pin_href = CAM_PIN_HREF;
        config.pin_sccb_sda = CAM_PIN_SIOD;
        config.pin_sccb_scl = CAM_PIN_SIOC;
        config.sccb_i2c_port = I2C_NUM_1;
        config.pin_pwdn = CAM_PIN_PWDN;
        config.pin_reset = CAM_PIN_RESET;
        config.xclk_freq_hz = 24000000;
        config.ledc_timer = LEDC_TIMER_0;
        config.ledc_channel = LEDC_CHANNEL_0;
        config.pixel_format = PIXFORMAT_RGB565;
        config.frame_size = FRAMESIZE_QVGA;
        config.jpeg_quality = 12;
        config.fb_count = 1;
        config.fb_location = CAMERA_FB_IN_PSRAM;
        config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

        camera_ = new Esp32Camera(config);
    }

public:
    atk_dnesp32s3() : boot_button_(BOOT_BUTTON_GPIO) {
        InitializeI2c();
        InitializeSpi();
        InitializeSt7789Display();
        InitializeButtons();
        InitializeCamera();
        InitializeVolumeKeys();
    }

    virtual Led* GetLed() override {
        static SingleLed led(BUILTIN_LED_GPIO);
        return &led;
    }

    virtual AudioCodec* GetAudioCodec() override {
        static Es8388AudioCodec audio_codec(
            i2c_bus_, 
            I2C_NUM_0, 
            AUDIO_INPUT_SAMPLE_RATE, 
            AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_MCLK, 
            AUDIO_I2S_GPIO_BCLK, 
            AUDIO_I2S_GPIO_WS, 
            AUDIO_I2S_GPIO_DOUT, 
            AUDIO_I2S_GPIO_DIN,
            GPIO_NUM_NC, 
            AUDIO_CODEC_ES8388_ADDR
        );
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }
    
    virtual Camera* GetCamera() override {
        return camera_;
    }
};

DECLARE_BOARD(atk_dnesp32s3);
