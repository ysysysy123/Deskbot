[CmdletBinding()]
param(
    [string]$IdfRoot = 'D:\Arduino\esp5.4\frameworks\esp-idf-v5.5.3',
    [string]$IdfTools = 'D:\Arduino\esp5.4',
    [string]$PythonEnv = 'D:\Arduino\esp5.4\python_env\idf5.5_py3.11_env'
)

$ErrorActionPreference = 'Stop'
$firmwareRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$project = Join-Path $firmwareRoot 'atk-dnesp32s3-eye-uart\source\xiaozhi-esp32'
$build = Join-Path $firmwareRoot 'atk-dnesp32s3-eye-uart\.build\atk-release'
$release = Join-Path $firmwareRoot 'atk-dnesp32s3-eye-uart\releases'
$python = Join-Path $PythonEnv 'Scripts\python.exe'
$idfPy = Join-Path $IdfRoot 'tools\idf.py'

foreach ($required in @($project, $python, $idfPy)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required path not found: $required"
    }
}

$env:IDF_TOOLS_PATH = $IdfTools
$env:IDF_PATH = $IdfRoot
$env:IDF_PYTHON_ENV_PATH = $PythonEnv
. (Join-Path $IdfRoot 'export.ps1')

# Some Windows PowerShell hosts keep separate PATH and Path entries. Collapse
# them before invoking child processes, then make Ninja explicit.
$pathValues = @()
for ($i = 0; $i -lt 4 -and (Test-Path Env:\Path); $i++) {
    $pathValues += (Get-Item Env:\Path).Value
    Remove-Item -LiteralPath Env:\Path
}
$env:Path = ($pathValues -join ';')
$ninjaDir = Get-ChildItem -LiteralPath (Join-Path $IdfTools 'tools\ninja') -Directory |
    Sort-Object Name -Descending | Select-Object -First 1
if ($null -eq $ninjaDir) { throw "Ninja was not found under $IdfTools" }
$env:Path = $ninjaDir.FullName + ';' + $env:Path

New-Item -ItemType Directory -Force -Path $build, $release | Out-Null
Push-Location $project
try {
    & $python $idfPy --no-ccache -B $build `
        -DIDF_TARGET=esp32s3 `
        -DBOARD_NAME=atk-dnesp32s3 `
        -DBOARD_TYPE=atk-dnesp32s3 build
    if ($LASTEXITCODE -ne 0) { throw "ATK build failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}

$app = Join-Path $release 'atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-app.bin'
Copy-Item -LiteralPath (Join-Path $build 'xiaozhi.bin') -Destination $app -Force
& $python -m esptool --chip esp32s3 merge_bin `
    -o (Join-Path $release 'atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-16mb.bin') `
    --flash_mode dio --flash_freq 80m --flash_size 16MB `
    0x0 (Join-Path $build 'bootloader\bootloader.bin') `
    0x8000 (Join-Path $build 'partition_table\partition-table.bin') `
    0xd000 (Join-Path $build 'ota_data_initial.bin') `
    0x20000 (Join-Path $build 'xiaozhi.bin') `
    0x800000 (Join-Path $build 'generated_assets.bin')
if ($LASTEXITCODE -ne 0) { throw "ATK image merge failed: $LASTEXITCODE" }

Get-FileHash -Algorithm SHA256 -LiteralPath $app, (Join-Path $release 'atk-dnesp32s3-xiaozhi-2.2.6-eye-uart-16mb.bin')
