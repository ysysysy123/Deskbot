[CmdletBinding()]
param(
    [string]$Port = 'COM11',
    [ValidateSet('Build', 'Full')]
    [string]$Mode = 'Build',
    [string]$ArduinoCli = 'D:\Arduino\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe',
    [string]$Python = 'D:\Arduino\esp5.4\python_env\idf5.5_py3.11_env\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'
$firmwareRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$build = Join-Path $firmwareRoot 'dualeye-eye-test\.build'
$release = Join-Path $firmwareRoot 'dualeye-eye-test\releases\dualeye-eye-test-esp32s3-16mb.bin'
$fqbn = 'esp32:esp32:esp32s3:UploadSpeed=921600,USBMode=hwcdc,CDCOnBoot=default,UploadMode=default,CPUFreq=240,FlashMode=qio120,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi'

if ($Mode -eq 'Build') {
    if (-not (Test-Path -LiteralPath $build)) { throw 'Build DualEye first with build-dualeye.ps1.' }
    & $ArduinoCli upload --fqbn $fqbn --port $Port --input-dir $build
} else {
    if (-not (Test-Path -LiteralPath $release)) { throw "Image not found: $release" }
    & $Python -m esptool --chip esp32s3 -p $Port -b 921600 `
        --before default_reset --after hard_reset write_flash 0x0 $release
}
if ($LASTEXITCODE -ne 0) { throw "DualEye flash failed: $LASTEXITCODE" }
