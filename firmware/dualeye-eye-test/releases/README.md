# DualEye 完整烧录包

- 文件：`dualeye-eye-test-esp32s3-16mb.bin`
- 板型：ESP32-S3 DualEye LCD 1.28（非触摸版）
- Arduino Core：`esp32:esp32 3.3.11`
- Flash / PSRAM：16 MB / 8 MB OPI
- 大小：16,777,216 字节
- SHA-256：`EF45A674120303113097975020FE0936B8C5D01D65B436C3FA7CFDB6E9014B25`
- 烧录地址：`0x0`

默认 COM11 完整恢复：

```powershell
& .\firmware\tools\flash-dualeye.ps1 -Mode Full
```

正常开发时先运行 `build-dualeye.ps1`，再使用默认 `flash-dualeye.ps1` 上传构建目录中的分段镜像。
