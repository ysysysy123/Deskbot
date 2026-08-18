[CmdletBinding()]
param(
    [string]$ArduinoCli = 'D:\Arduino\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe',
    [string]$Libraries = ''
)

$ErrorActionPreference = 'Stop'
$firmwareRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sketch = Join-Path $firmwareRoot 'dualeye-eye-test'
if ([string]::IsNullOrWhiteSpace($Libraries)) {
    $Libraries = Join-Path $sketch 'libraries'
}
$build = Join-Path $sketch '.build'
$release = Join-Path $sketch 'releases'
$fqbn = 'esp32:esp32:esp32s3:UploadSpeed=921600,USBMode=hwcdc,CDCOnBoot=default,UploadMode=default,CPUFreq=240,FlashMode=qio120,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi'

foreach ($required in @($ArduinoCli, $Libraries, $sketch)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required path not found: $required" }
}
New-Item -ItemType Directory -Force -Path $build, $release | Out-Null
& $ArduinoCli compile --fqbn $fqbn --libraries $Libraries --build-path $build $sketch
if ($LASTEXITCODE -ne 0) { throw "DualEye build failed: $LASTEXITCODE" }

$image = Join-Path $release 'dualeye-eye-test-esp32s3-16mb.bin'
Copy-Item -LiteralPath (Join-Path $build 'dualeye-eye-test.ino.merged.bin') -Destination $image -Force
Get-FileHash -Algorithm SHA256 -LiteralPath $image
