# 桌面宠物固件

本目录可以直接随仓库上传 GitHub，包含两块主板的源码、编译/烧录入口和可恢复固件。

## 目录

| 路径 | 内容 |
| --- | --- |
| `atk-dnesp32s3-eye-uart/source/xiaozhi-esp32/` | ATK-DNESP32S3 小智 2.2.6 完整源码 |
| `atk-dnesp32s3-eye-uart/releases/` | ATK 应用包及 16 MB 完整包 |
| `dualeye-eye-test/` | DualEye Arduino 源码及本地 LVGL 8.3.10 |
| `dualeye-eye-test/releases/` | DualEye 16 MB 完整包 |
| `tools/` | 两块板的编译和烧录脚本 |
| `hal/`、`services/`、`orchestrator/` | 后续桌宠电机和行为控制分层代码 |

## 快速使用

```powershell
# ATK 编译、生成应用包和完整包
& .\firmware\tools\build-atk.ps1

# ATK 应用更新，默认 COM12，保留 Wi-Fi/绑定
& .\firmware\tools\flash-atk.ps1

# DualEye 编译和烧录，默认 COM11
& .\firmware\tools\build-dualeye.ps1
& .\firmware\tools\flash-dualeye.ps1
```

详细参数和完整恢复方式见 [tools/README.md](tools/README.md)。临时依赖、构建对象、ELF、MAP 和 `sdkconfig` 均由 `.gitignore` 排除；`releases/*.bin` 保留用于发布。
