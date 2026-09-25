# 固件编译与烧录入口

当前立创实战派 ESP32-S3 的 Ubuntu/Docker 构建入口由上游工程提供，见：

```text
firmware/lichuang-s3/source/xiaozhi-esp32/docker/firmware-builder/
```

公共仓库只保留板级源码和可复现的构建说明；构建目录、日志和中间产物统一放在：

```text
/data/workspace/deskbot_work/build/
```

DualEye 的 Arduino 编译/烧录脚本仍保留：

```powershell
& .\firmware\tools\build-dualeye.ps1
& .\firmware\tools\flash-dualeye.ps1
```
