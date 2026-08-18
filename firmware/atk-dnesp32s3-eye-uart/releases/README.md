# ATK-DNESP32S3 一体烧录包

文件：`atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-16mb.bin`

- 小智版本：2.2.6
- ESP-IDF：5.5.3
- 芯片/Flash/PSRAM：ESP32-S3 / 16 MB / 8 MB OPI
- 板型：`atk-dnesp32s3`
- 摄像头：正点原子 OV5640 DVP，24 MHz XCLK，RGB565/QVGA 320×240
- 双眼串口：UART1，TX GPIO43，RX GPIO44，115200 8N1
- 双眼同步：自动 `PING`，收到 `READY`/`PONG` 后重发当前状态
- 控制台：USB Serial/JTAG，不占 GPIO43/44
- 整包烧录地址：`0x0`
- 文件长度：16,384,840 字节
- 完整包 SHA-256：`FCBCA73630FF2AC576A3F369DC62069BC7622A12572C52C6C82058937E4EFFF8`
- 应用包 SHA-256：`D0FA76483086DBE5EE880671D0880123E9B53F92FA28B68D165E2AC783482EF4`

2026-08-18 构建和 COM12 应用分区烧录均已通过；实机已识别 OV5640（PID `0x5640`），取得完整 320×240 帧并成功完成 JPEG 上传和真实场景描述。此包还包含 HTTP 被动断开竞态修复。双眼 UART 仍须以 `DualEye link established` 和 `OK STATE ...` 回包完成物理接线验收；板对板 USB-C 直连不承载当前 UART 协议。

COM12 烧录：

```powershell
$PYTHON = "D:\Arduino\esp5.4\python_env\idf5.5_py3.11_env\Scripts\python.exe"
$ESPTOOL = "D:\Arduino\esp5.4\frameworks\esp-idf-v5.5.3\components\esptool_py\esptool\esptool.py"
$BIN = "D:\desktop\xiaozhi\Deskbot\firmware\atk-dnesp32s3-eye-uart\releases\atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-16mb.bin"

& $PYTHON $ESPTOOL --chip esp32s3 -p COM12 -b 460800 `
  --before default_reset --after hard_reset write_flash `
  --flash_mode dio --flash_freq 80m --flash_size 16MB `
  0x0 $BIN
```

注意：一体包从 `0x0` 连续写到资源区，空隙以 `0xFF` 填充，因此也会清空位于 `0x9000` 的 NVS（Wi-Fi、绑定等设备配置）。它适合首次安装或完整恢复；烧录后需要重新配网/绑定。只更新程序时应使用构建目录的分段 `idf.py flash` 或仅写 bootloader 和应用，不要刷这个整包。
