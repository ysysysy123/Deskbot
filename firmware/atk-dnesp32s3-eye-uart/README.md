# ATK-DNESP32S3 到 DualEye 的 UART 发送模块

本目录现在包含可独立编译的小智 2.2.6 源码、双眼 UART 适配层、发布固件和操作补丁。当前已在 ESP-IDF 5.5.3、ATK-DNESP32S3 上编译并通过 COM12 实机启动测试。

完整工程位于 [source/xiaozhi-esp32](source/xiaozhi-esp32)。第三方依赖由 `dependencies.lock` 锁定；修复后的 `78/esp-ml307` 作为本地组件放在工程的 `components/78__esp-ml307`，因此从 GitHub 克隆后无需再手工修改 `managed_components`。约 723 MiB 的 `managed_components` 只是可再下载的构建缓存，已从源码目录清除且不会上传 GitHub；首次编译会自动恢复。

这是面向本机 ATK-DNESP32S3 的裁剪版本。`main/boards` 仅保留 `atk-dnesp32s3` 和编译时直接依赖的 `common`；其他上游板型源码已移除。`Kconfig.projbuild` 和 `CMakeLists.txt` 仍保留上游板型选项，便于以后同步源码，但本仓库只保证 `CONFIG_BOARD_TYPE_ATK_DNESP32S3` 可以构建。

使用方法：

1. 将 `eye_uart_link.h` 和 `eye_uart_link.cc` 复制到小智工程的 `main` 目录。
2. 在 `main/CMakeLists.txt` 的 `SOURCES` 中加入 `"eye_uart_link.cc"`。
3. 在 `application.cc` 中包含 `eye_uart_link.h`，初始化后根据该版本的 `DeviceState` 调用 `EyeUartLink::SendState()`。
4. 仅在 `CONFIG_BOARD_TYPE_ATK_DNESP32S3` 下启用这些调用，避免影响其他板型。状态映射放在 `application.cc`，串口模块本身只发送协议文本。
5. 把 ESP-IDF 主控制台改为 `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`，并选择 `CONFIG_ESP_CONSOLE_SECONDARY_NONE=y`；否则 UART0 日志也会从默认 GPIO43/44 输出，污染双眼协议。

模块会在尚未收到回包时自动发送 `PING`，收到 `READY`/`PONG` 后自动重发最近一次状态。日志出现 `DualEye link established` 才表示物理 UART 已建立；DualEye 自己的 `IDLE` 眨眼不代表联动成功。

当前 ATK 摄像头按照片和正点原子例程配置为 OV5640 DVP、24 MHz XCLK、RGB565/QVGA；绿色线条修复和 HTTP 断开竞态修复保存在 [camera-network-fix.patch](camera-network-fix.patch)。

两板之间直接插 USB-C 线不能替代 UART：当前 ATK 与 DualEye 固件都没有实现一端 USB Host、另一端 USB CDC Device 的板间协议。要使用现有固件，仍需从 GPIO43/44 交叉连接 TX/RX，并连接 GND。

完整接线、集成代码片段和测试顺序见 [../../docs/dual-board-uart-architecture.md](../../docs/dual-board-uart-architecture.md)。

可直接从地址 `0x0` 烧录的一体包位于 [releases](releases)。本次构建和 COM12 测试记录见 [../../docs/atk-com12-flash-test.md](../../docs/atk-com12-flash-test.md)。

统一的编译和烧录入口见 [../tools/README.md](../tools/README.md)。
