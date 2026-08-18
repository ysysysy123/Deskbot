# ESP32-S3 DualEye 桌宠眼睛：操作与改进指南

## 当前已验证的硬件与固件

- 板卡：Waveshare `ESP32-S3-DualEye-LCD-1.28` **非触摸版**。
- 连接：`COM11`，USB-Serial/JTAG。
- 芯片：ESP32-S3，16 MB Flash，8 MB 内置 OPI PSRAM。
- Arduino CLI：`D:\Arduino\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe`。
- 已安装 Core：`esp32:esp32 3.3.11`。官方示例目录名为 `Arduino-3.2.0`；本项目已在 3.3.11 下实际编译、烧录并验证显示正常。
- 当前程序：[../firmware/dualeye-eye-test](../firmware/dualeye-eye-test)。它来自官方 `01_LCD_Driver` 的独立副本，只修改了 `LVGL_Example.cpp` 的界面层。

当前效果：两个圆屏均为黑色背景、椭圆白色眼球、彩色虹膜、黑色瞳孔和双高光。默认视线会随机选择目标，以缓动动画移动并自然停顿；两只眼睛会同步眨眼。程序现在还支持 UART 控制的 `IDLE`、`LISTENING`、`THINKING`、`SPEAKING`、`HAPPY`、`SLEEPING` 六种状态。

ATK-DNESP32S3 的接线、协议、状态映射、电机引脚和供电方案见 [双板 UART 架构](dual-board-uart-architecture.md)。

### 本版复用的本地官方示例

- 非触摸版 `01_LCD_Driver`：保留双屏初始化、`disp`/`disp2` 和 GC9A01 驱动。
- 非触摸版 `04_LVGL_Arduino`：沿用 `lv_timer_create` 驱动界面状态以及重新初始化时清理定时器/动画的方式。
- LVGL `examples/anim`：使用 `lv_anim_path_ease_in_out` 平滑移动视线，并使用 playback 动画完成“闭眼后自动睁眼”。

触摸版示例中的触摸和画板代码没有合入，因为当前硬件是非触摸版；麦克风、SD 和网络代码也仍与眼睛动画解耦。

## 一次性连接与确认

1. 仅连接 DualEye 主板自己的 Type-C 数据线，不要接其他 ESP32 开发板。
2. Windows 应出现 `USB 串行设备 (COM11)`；Arduino CLI 的 `board list` 会将它识别为 ESP32 Family Device。
3. 如自动烧录无法连接，按住板上的 `BOOT`，重新插 USB 或按 `RESET`，再松开 `BOOT` 后重试。
4. 不要在通电时插拔 LCD/FPC 排线。

只读识别命令：

```powershell
$ARDUINO_CLI = 'D:\Arduino\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'
& $ARDUINO_CLI board list
```

期望看到 `COM11`。烧录时日志中的 `Hash of data verified` 是成功的必要确认；屏幕实际亮起并显示眼睛才是最终确认。

## 构建与烧录当前眼睛程序

在仓库根目录执行。所需的精简版 LVGL 8.3.10 已保存在 DualEye 源码的 `libraries` 下，不再依赖厂商示例的绝对路径。

```powershell
$ARDUINO_CLI = 'D:\Arduino\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'
$LIBRARIES = 'D:\desktop\xiaozhi\Deskbot\firmware\dualeye-eye-test\libraries'
$SKETCH = 'D:\desktop\xiaozhi\Deskbot\firmware\dualeye-eye-test'
$BUILD = 'D:\desktop\xiaozhi\Deskbot\firmware\dualeye-eye-test\.build'
$FQBN = 'esp32:esp32:esp32s3:UploadSpeed=921600,USBMode=hwcdc,CDCOnBoot=default,UploadMode=default,CPUFreq=240,FlashMode=qio120,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi'

& $ARDUINO_CLI compile --fqbn $FQBN --libraries $LIBRARIES --build-path $BUILD $SKETCH
& $ARDUINO_CLI upload --fqbn $FQBN --port COM11 --input-dir $BUILD
```

也可以直接使用仓库脚本；编译后会同时更新 `dualeye-eye-test/releases` 下的 16 MB 完整恢复包：

```powershell
& .\firmware\tools\build-dualeye.ps1
& .\firmware\tools\flash-dualeye.ps1
```

关键配置不能省略：`FlashSize=16M`、`PSRAM=opi`、`FlashMode=qio120`，以及 16 MB 的 `app3M_fat9M_16MB` 分区。

> 烧录会替换应用分区。此前尝试读取全量 Flash 备份时，COM11 的长时间连续读取在约 1.3% 处中断，因此不要把未验证的临时备份当作可恢复文件。

## 怎样改眼睛

主要只编辑 [LVGL_Example.cpp](../firmware/dualeye-eye-test/LVGL_Example.cpp)。不要修改 `LCD_Driver.*`、`Display_GC9A01.*` 或 `Board_Configuration.h`，除非是在处理底层显示故障。

当前结构：

```text
Lvgl_Example1()
  ├─ create_eye(disp)   -> 左屏
  ├─ create_eye(disp2)  -> 右屏
  └─ controller_timer_cb()
       ├─ process_commands()  -> 处理 UART 入队的状态/视线/眨眼命令
       ├─ update_automatic_motion() -> 根据当前状态生成动作
       └─ start_blink()       -> 两只眼睛同步闭合并自动睁开
```

常用可调参数：

| 目标 | 位置 | 建议修改 |
| --- | --- | --- |
| 眼球大小 | `EYE_WIDTH` / `EYE_HEIGHT` | 当前 176 × 164 |
| 虹膜大小 | `IRIS_SIZE` | 当前 74 |
| 瞳孔大小 | `PUPIL_SIZE` | 当前 40 |
| 视线目标 | `targets[][2]` | 增删 `{x, y}` 坐标 |
| 移动耗时 | `random_between(280, 560)` | 数值越大越慢 |
| 视线停顿 | `random_between(900, 2600)` | 单位为 ms |
| 眨眼间隔 | `random_between(2800, 6500)` | 单位为 ms |
| 闭眼速度 | `lv_anim_set_time(..., 90)` | 单位为 ms |

每次修改后都先运行 `compile`；只有编译成功，再运行 `upload`。

## 下一步改进路线

按这个顺序扩展，便于定位问题：

1. **实机 UART 联调：** 完成 ATK P4 跳帽、TX/RX/GND 三线连接，逐条验证 `PING`、六种 `STATE`、`GAZE` 和 `BLINK`。
2. **小智联动（已完成）：** 小智 2.2.6 的 `DeviceStateMachine` 监听器已通过 `EyeUartLink::SendState()` 映射到双眼状态；网络和音频仍只在 ATK 侧处理。
3. **启动重同步：** ATK 收到 `READY EYE_UART_V1` 后重发当前状态，避免任一主板后启动导致状态丢失。
4. **电机控制：** 停用不需要的 ATK SPI LCD 后再分配 DRV8833 四路 PWM，先单轮低速测试供电噪声。
5. **项目可移植性：** 将当前 `$LIBRARIES` 路径中的必要库版本纳入项目依赖说明或固定下载脚本，避免换电脑后依赖本机目录。

## 常见问题

| 现象 | 优先检查 |
| --- | --- |
| `Connecting...` 一直不成功 | 按住 `BOOT` 后重新连接，再烧录；确认 COM11 没被串口监视器占用。 |
| 电脑找不到 COM11 | 换一条数据线、换 USB 口，并确认板子有电。 |
| 编译提示主 `.ino` 缺失 | Arduino 要求目录名与主 `.ino` 文件名相同；当前两者都是 `dualeye-eye-test`。 |
| 写入成功但黑屏 | 先重新烧录官方 `01_LCD_Driver` 排除硬件问题；检查 FPC 排线断电后是否插牢。 |
| 只有一只眼睛异常 | 保持软件不动，优先检查对应屏幕的排线和物理连接。 |
