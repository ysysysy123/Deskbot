[CmdletBinding()]
param(
    [string]$Port = 'COM12',
    [ValidateSet('App', 'Full')]
    [string]$Mode = 'App',
    [string]$Python = 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'
$release = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..')).Path 'atk-dnesp32s3-eye-uart\releases'

if ($Mode -eq 'App') {
    $image = Join-Path $release 'atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-app.bin'
    $offset = '0x20000'
} else {
    $image = Join-Path $release 'atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-16mb.bin'
    $offset = '0x0'
    Write-Warning 'Full mode clears Wi-Fi, binding, and other NVS settings.'
}

if (-not (Test-Path -LiteralPath $image)) { throw "Image not found: $image" }
& $Python -m esptool --chip esp32s3 -p $Port -b 460800 `
    --before default_reset --after hard_reset write_flash `
    --flash_mode dio --flash_freq 80m --flash_size 16MB `
    $offset $image
if ($LASTEXITCODE -ne 0) { throw "ATK flash failed: $LASTEXITCODE" }

