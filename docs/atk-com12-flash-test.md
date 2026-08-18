# ATK-DNESP32S3 COM12 烧录与测试记录

## 本次结果

2026-08-18 已把本地小智 2.2.6 源码与双眼 UART 模块集成，使用 ESP-IDF 5.5.3 编译并烧录到 COM12。根据实物和 PID 确认摄像头是 **OV5640**。最终实机配置采用正点原子独立例程同类路径：`esp32-camera`、RGB565、QVGA 320×240、24 MHz XCLK、单帧 PSRAM 缓冲。

实机确认：

- ESP32-S3 rev 0.2、16 MB Flash、8 MB OPI PSRAM；
- 板型 `atk-dnesp32s3`，LCD/LVGL 和 ES8388 麦克风/扬声器初始化成功；
- `EyeUart` 使用 UART1，TX GPIO43、RX GPIO44、115200 8N1；
- 控制台只走 USB Serial/JTAG，GPIO43/44 不再输出系统日志；
- Wi-Fi 成功连接并获得 IP；
- OTA 确认 2.2.6 为最新版本；
- MQTT 成功连接 `mqtt.xiaozhi.me`，设备完成激活并进入 `idle`；
- “你好小智”唤醒模型已加载，实测进入 listening/speaking 并收到服务端回复。
- 拍照得到完整 `320x240 / 153600` 字节帧，JPEG 编码和上传成功；服务端已识别出实际室内墙面、家具和织物，不再是整幅绿色线条。
- 修复服务端先断开 HTTP 时接收任务仍访问已析构互斥锁的竞态；该问题此前会触发 `xQueueSemaphoreTake` 或 `spinlock_acquire` 断言重启。

初版先误配为 OV2640，后来切到 `EspVideo` 的 OV5640 YUV422 800×600 后虽然能出帧，但画面是绿色色块和横向错位。仅改 YUV 字节顺序仍未解决。最终改为已被该硬件独立例程验证的 RGB565/QVGA 路径后，实机日志出现 `camera: Detected OV5640 camera`、`Camera initialized: format=0` 和完整帧长度 `153600`，并成功注册和调用 `self.camera.take_photo`。

双眼联动尚未通过物理链路验收：COM12 日志只有 ATK 发送，没有 `READY EYE_UART_V1`、`PONG 1` 或 `OK STATE ...` 回包。两块 ESP32-S3 的 USB-C 口当前都作为 USB Device 使用，板对板直接插 USB-C 线不会自动变成 UART，也不会把 GPIO43/44 的协议送到另一块板。当前固件必须另接 TX、RX、GND 三线 UART。

## 源码选择结论

厂商 A 盘里的 0.9.9 工程可以编译和启动，但当前 `mqtt.xiaozhi.me` 会关闭它的旧协议连接，按键后提示无法连接服务，因此不再用于最终固件。

最终基于：

```text
D:\desktop\xiaozhi\ESP32-S3-DualEye-Touch-LCD-1.28\xiaozhi-esp32
```

仓库内可编译源码：

```text
D:\desktop\xiaozhi\Deskbot\firmware\atk-dnesp32s3-eye-uart\source\xiaozhi-esp32
```

厂商原始目录没有被修改。依赖下载和构建输出也已经迁移到 `firmware` 下，并由 Git 忽略。

## 重新编译

板型和控制台配置必须存在于 `sdkconfig.defaults`：

```text
CONFIG_BOARD_TYPE_ATK_DNESP32S3=y
CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y
CONFIG_ESP_CONSOLE_SECONDARY_NONE=y
```

摄像头的最终格式、分辨率和时钟由 `main/boards/atk-dnesp32s3/atk_dnesp32s3.cc` 中的 `camera_config_t` 决定，不再依赖 `EspVideo` 的 YUV menuconfig 项。可重复补丁保存在 [camera-network-fix.patch](../firmware/atk-dnesp32s3-eye-uart/camera-network-fix.patch)，在 2.2.6 工程根目录执行 `git apply --check` 后再 `git apply`。

推荐直接使用仓库脚本；它固定 ESP-IDF 5.5.3、ESP32-S3 目标、板型、构建目录和发布包合并步骤：

```powershell
& .\firmware\tools\build-atk.ps1
```

本机 ccache 在高并发构建时出现过挂起，因此脚本显式使用 `--no-ccache`。完整手工命令仍可在源码目录执行：

```powershell
$env:IDF_TOOLS_PATH = "D:\Arduino\esp5.4"
$env:IDF_PATH = "D:\Arduino\esp5.4\frameworks\esp-idf-v5.5.3"
$env:IDF_PYTHON_ENV_PATH = "D:\Arduino\esp5.4\python_env\idf5.5_py3.11_env"
. "$env:IDF_PATH\export.ps1"

$PYTHON = "$env:IDF_PYTHON_ENV_PATH\Scripts\python.exe"
$IDF_PY = "$env:IDF_PATH\tools\idf.py"

& $PYTHON $IDF_PY --no-ccache -B build-atk-release `
  -DBOARD_NAME=atk-dnesp32s3 `
  -DBOARD_TYPE=atk-dnesp32s3 build
```

完整的一体包及重刷命令见 [ATK 发布目录](../firmware/atk-dnesp32s3-eye-uart/releases/README.md)。一体包会清空 NVS，适合首次安装/恢复；修正版应优先只写应用分区，以保留现有 Wi-Fi 和绑定配置。

如果只更新程序并保留 Wi-Fi/绑定，使用下列应用分区烧录；本次修正版就是按此方式成功写入：

```powershell
$PYTHON = "D:\Arduino\esp5.4\python_env\idf5.5_py3.11_env\Scripts\python.exe"
$APP = "D:\desktop\xiaozhi\Deskbot\firmware\atk-dnesp32s3-eye-uart\releases\atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-app.bin"

& $PYTHON -m esptool --chip esp32s3 -p COM12 -b 460800 `
  --before default_reset --after hard_reset write_flash `
  --flash_mode dio --flash_freq 80m --flash_size 16MB `
  0x20000 $APP
```

## 你现在的测试顺序

1. 先不接电机，只给 ATK 通 USB，屏幕应进入待命；说“你好小智”，确认能听到回复。
2. 对小智说“拍张照片并描述”；COM12 应出现 `Captured frame: 320x240, len=153600`、JPEG 编码和上传成功，且描述应对应镜头前真实物体。
3. ATK 与 DualEye 各自 USB 供电并共地；拔掉 ATK P4 的两只竖向 UART0/CH340 跳帽。P4 顶排 1/2 是 CH340，中排 3/4 才是 ESP32 UART。
4. P4-4（GPIO43/TX）接 DualEye 14P-9（GPIO44/RX）；P4-3（GPIO44/RX）接 DualEye 14P-10（GPIO43/TX）。不要接 P4 顶排 1/2。
5. 不要把当前板对板 USB-C 线当作 UART。查看 COM12：三线 UART 接好后必须看到 `EyeUart: DualEye link established`，随后还应看到 `READY`/`PONG` 和 `OK STATE ...`；仅看到静态眼睛或自动眨动都不算联动成功。
6. UART 稳定后再接 DRV8833 和电机电源，电机电源不要来自任一主板的 3.3 V。

## 后续改进优先级

1. 已实现：未握手时自动发送 `PING`；收到 `READY`/`PONG` 后重发当前状态，处理两块主板启动先后不同。
2. 待实机验证后再决定是否给 UART 帧增加序号/校验，不先增加复杂度。
3. 已完成拍照 MCP 实测；后续如需提高分辨率，应逐级测试 QVGA → VGA，并以完整帧长度、实图和连续拍照不重启为验收条件。
4. 电机控制先做单轮低速、限时停机和欠压保护，再扩展双轮运动。
5. 最终合并供电前测量电机启动压降和音频噪声，必要时给电机单独稳压并加强去耦。
