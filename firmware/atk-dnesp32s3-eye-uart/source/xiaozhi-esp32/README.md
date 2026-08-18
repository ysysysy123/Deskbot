# ATK-DNESP32S3 小智固件源码

这是 Deskbot 使用的本地裁剪工程，仅支持 `ATK-DNESP32S3`（ESP32-S3、16 MB Flash、中文资源）以及与 DualEye 的 UART 联动。

工程只保留当前固件需要的板级源码、中文与英文回退资源、16 MB 分区表和构建生成脚本。`managed_components` 与 `sdkconfig` 是可再生成文件，均由 `.gitignore` 排除：首次编译会按照 `dependencies.lock` 下载依赖并根据 `sdkconfig.defaults` 生成配置。

从仓库根目录执行：

```powershell
.\firmware\tools\build-atk.ps1
```

板型说明、接线、烧录和测试方法见上级目录的 [README](../../README.md) 与仓库的 `docs` 目录。

上游项目许可证见 [LICENSE](LICENSE)。
