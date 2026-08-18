# 固件编译与烧录入口

所有命令从仓库根目录运行：

```powershell
# ATK：编译，并同时生成应用包和 16 MB 完整包
& .\firmware\tools\build-atk.ps1

# ATK：只更新应用，保留 Wi-Fi 和设备绑定（默认 COM12）
& .\firmware\tools\flash-atk.ps1

# ATK：完整恢复，会清空 Wi-Fi 和设备绑定
& .\firmware\tools\flash-atk.ps1 -Mode Full

# DualEye：编译和烧录（默认 COM11）
& .\firmware\tools\build-dualeye.ps1
& .\firmware\tools\flash-dualeye.ps1

# DualEye：没有构建目录时，用仓库内的 16 MB 完整包恢复
& .\firmware\tools\flash-dualeye.ps1 -Mode Full
```

ATK 的临时文件位于 `firmware/atk-dnesp32s3-eye-uart/.build/`，DualEye 的临时文件位于 `firmware/dualeye-eye-test/.build/`；两者均被 Git 忽略。可发布 BIN 分别保存在两套固件自己的 `releases/` 中。
