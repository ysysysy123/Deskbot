#include "display_manager.h"
#include "esp_log.h"
#include <esp_lvgl_port.h>
#include <esp_psram.h>

static const char* TAG = "SpiLcdDisplayExt";

std::vector<Display*> DisplayManager::displays_;
Display* DisplayManager::primary_display_ = nullptr;

void DisplayManager::AddDisplay(Display* display) {
    if (!display) return;
    
    displays_.push_back(display);
    primary_display_ = display;
    ESP_LOGI("DisplayManager", "Display added, total: %d", displays_.size());
}

void DisplayManager::RemoveDisplay(Display* display) {
    auto it = std::find(displays_.begin(), displays_.end(), display);
    if (it != displays_.end()) {
        displays_.erase(it);
        if (primary_display_ == display) {
            primary_display_ = displays_.empty() ? nullptr : displays_[0];
        }
    }
}

size_t DisplayManager::GetDisplayCount() {
    return displays_.size();
}

Display* DisplayManager::GetPrimaryDisplay() {
    return primary_display_;
}

const std::vector<Display*>& DisplayManager::GetAllDisplays() {
    return displays_;
}

// 应用到所有屏幕
void DisplayManager::SetEmotion(const char* emotion) {
    for (auto display : displays_) {
        if (display) display->SetEmotion(emotion);
    }
}

void DisplayManager::SetPowerSaveMode(bool on) {
    for (auto display : displays_) {
        if (display) display->SetPowerSaveMode(on);
    }
}

void DisplayManager::SetChatMessage(const char* role, const char* content) {
    for (auto display : displays_) {
        if (display) display->SetChatMessage(role, content);
    }
}

void DisplayManager::SetTheme(Theme* theme) {
    for (auto display : displays_) {
        if (display) display->SetTheme(theme);
    }
}

Theme* DisplayManager::GetTheme() {
    if (primary_display_) {
        return primary_display_->GetTheme();
    }
    return nullptr;
}

void DisplayManager::SetStatus(const char* status) {
    for (auto display : displays_) {
        if (display) display->SetStatus(status);
    }
}
void DisplayManager::ShowNotification(const char* message, int duration_ms) {
    for (auto display : displays_) {
        if (display) {
            display->ShowNotification(message, duration_ms);
        }
    }
}

void DisplayManager::ShowNotification(const std::string& notification, int duration_ms) {
    for (auto display : displays_) {
        if (display) {
            display->ShowNotification(notification, duration_ms);
        }
    }
}

void DisplayManager::UpdateStatusBar(bool update_all) {
    for (auto display : displays_) {
        if (display) {
            display->UpdateStatusBar(update_all);
        }
    }
}

bool DisplayManager::Lock(int timeout_ms) {
    return true;
}

void DisplayManager::Unlock() {
}

SpiLcdDisplayExtended::SpiLcdDisplayExtended(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
                           int width, int height, int offset_x, int offset_y, bool mirror_x, bool mirror_y, bool swap_xy
                           )
    : LcdDisplay(panel_io, panel, width, height) {

    // draw white
    std::vector<uint16_t> buffer(width_, 0xFFFF);
    for (int y = 0; y < height_; y++) {
        esp_lcd_panel_draw_bitmap(panel_, 0, y, width_, y + 1, buffer.data());
    }

    // Set the display to on
    ESP_LOGI(TAG, "Turning display on");
    {
        esp_err_t __err = esp_lcd_panel_disp_on_off(panel_, true);
        if (__err == ESP_ERR_NOT_SUPPORTED) {
            ESP_LOGW(TAG, "Panel does not support disp_on_off; assuming ON");
        } else {
            ESP_ERROR_CHECK(__err);
        }
    }
    //Initialize LVGL only once
    static bool lvgl_inited = false;
    if (!lvgl_inited) {
        ESP_LOGI(TAG, "Initialize LVGL library");
        lv_init();

    // #if CONFIG_SPIRAM
    //     // lv image cache, currently only PNG is supported
    //     size_t psram_size_mb = esp_psram_get_size() / 1024 / 1024;
    //     if (psram_size_mb >= 8) {
    //         lv_image_cache_resize(2 * 1024 * 1024, true);
    //         ESP_LOGI(TAG, "Use 2MB of PSRAM for image cache");
    //     } else if (psram_size_mb >= 2) {
    //         lv_image_cache_resize(512 * 1024, true);
    //         ESP_LOGI(TAG, "Use 512KB of PSRAM for image cache");
    //     }
    // #endif

        ESP_LOGI(TAG, "Initialize LVGL port");
        lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
        port_cfg.task_priority = 1;
    #if CONFIG_SOC_CPU_CORES_NUM > 1
        port_cfg.task_affinity = 1;
    #endif
        lvgl_port_init(&port_cfg);
        lvgl_inited = true;
    }

    ESP_LOGI(TAG, "Adding LCD display");
    const lvgl_port_display_cfg_t display_cfg = {
        .io_handle = panel_io_,
        .panel_handle = panel_,
        .control_handle = nullptr,
        .buffer_size = static_cast<uint32_t>(width_ * 20),
        .double_buffer = false,
        .trans_size = 0,
        .hres = static_cast<uint32_t>(width_),
        .vres = static_cast<uint32_t>(height_),
        .monochrome = false,
        .rotation = {
            .swap_xy = swap_xy,
            .mirror_x = mirror_x,
            .mirror_y = mirror_y,
        },
        .color_format = LV_COLOR_FORMAT_RGB565,
        .flags = {
            .buff_dma = 1,
            .buff_spiram = 0,
            .sw_rotate = 0,
            .swap_bytes = 1,
            .full_refresh = 0,
            .direct_mode = 0,
        },
    };

    display_ = lvgl_port_add_disp(&display_cfg);
    if (display_ == nullptr) {
        ESP_LOGE(TAG, "Failed to add display");
        return;
    }

    if (offset_x != 0 || offset_y != 0) {
        lv_display_set_offset(display_, offset_x, offset_y);
    }

    lv_display_set_default(display_);

    SetupUI();
}
