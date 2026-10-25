# 立创·实战派 ESP32-S3 硬件原理图与设计资料

本目录收录了 Deskbot 所采用的**立创·实战派 ESP32-S3 开发板**及周边配套硬件的完整官方原理图、接口定义与数据手册。

---

## 一、主板原理图文件（版本：ESP32-S3-V1_0_1）

| 文件名 | 内容描述 | 关键外设 / 芯片 |
| :--- | :--- | :--- |
| **[原理图-1.png](原理图-1.png)** | 主控核心与系统总线 | ESP32-S3-WROOM-1 模组、外扩接口、I2C 总线、QMI8658 6轴姿态传感器、PCA9557 IO 扩展芯片、MicroSD/TF 卡槽 |
| **[原理图-2.png](原理图-2.png)** | 供电、视觉与屏幕显示 | USB Type-C 接口、DC-DC 与 LDO 供电链路、GC2145 DVP 摄像头 24-Pin FPC 接口、GT911 电容触摸接口（J5）、ST7789 2.4 寸 TFT 屏幕接口（J4） |
| **[原理图-3.png](原理图-3.png)** | 音频输入与输出链路 | ES8311 音频 DAC 功放电路、ES7210 4-Mic 麦克风阵列 ADC 录音电路、板载喇叭接口 |

---

## 二、屏幕与周边配件资料

- **[TFT-H013A6QQIGC5N10.pdf](TFT-H013A6QQIGC5N10.pdf)**：ST7789 屏幕规格说明书与 10-Pin 排线引脚图纸。
- **[DualEye-Schematic.pdf](DualEye-Schematic.pdf)** / **[DualEye-Schematic.png](DualEye-Schematic.png)**：双目动态表情副屏与串口扩展配件板原理图。

---

## 三、屏幕接口 J4 引脚定义速查（连接器型号：AFC34-S10FA-00）

| Pin | 原理图信号 | ESP32-S3 连接 | 说明 |
| :---: | :---: | :---: | :--- |
| 1 | **DC** | GPIO 39 | 数据/命令控制引脚 |
| 2 | **SDA** | GPIO 40 | SPI MOSI 数据线 |
| 3 | **CS** | PCA9557 IO0 | 片选引脚（由 I2C IO 扩展芯片控制） |
| 4 | **RST** | RESET | 硬件复位网络 |
| 5 | **SCL** | GPIO 41 | SPI SCLK 时钟线 |
| 6 | **GND** | GND | 电源地 |
| 7 | **VDD** | 3V3 | 逻辑电源 3.3V |
| 8 | **GND** | GND | 电源地 |
| 9 | **BL_A** | 3V3 经 Q2 高端开关 | 背光阳极（由 GPIO 42 控制 PWM 调光） |
| 10 | **BL_K** | GND | 背光阴极 |

---

## 四、立创官方开源工程与在线原理图源地址

- **立创开发板官方文档 Wiki**：
  [https://wiki.lckfb.com/zh-hans/szpi-esp32s3/open-source-hardware/](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/open-source-hardware/)
- **嘉立创 EDA 在线开源硬件工程（可直接在线打开、修改和导出 Gerber/BOM）**：
  [https://oshwhub.com/li-chuang-kai-fa-ban/li-chuang-shi-zhan-pai-esp32-s3-kai-fa-ban](https://oshwhub.com/li-chuang-kai-fa-ban/li-chuang-shi-zhan-pai-esp32-s3-kai-fa-ban)
