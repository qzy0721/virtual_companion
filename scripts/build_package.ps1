[CmdletBinding()]
param(
    [string]$Python = "D:/Anaconda/envs/virtual_accompany/python.exe",
    [string]$OutputDirectory = "",
    [switch]$CleanUserData,
    [switch]$CopyProjectEnv,
    [switch]$RunAfterBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $ProjectRoot "web"
$ShellProject = Join-Path $ProjectRoot "desktop/VirtualCompanion.WinForms/VirtualCompanion.WinForms.csproj"
$SpecPath = Join-Path $ProjectRoot "packaging/virtual_companion.spec"
$BuildRoot = Join-Path $ProjectRoot "build"
$ServerDistRoot = Join-Path $BuildRoot "server-dist"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller"
$LocalAppDataPath = Join-Path $env:LOCALAPPDATA "VirtualCompanion"
$ProjectEnvPath = Join-Path $ProjectRoot ".env"

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot "dist"
}

function Remove-SafeDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $rootPath = [System.IO.Path]::GetPathRoot($fullPath)
    $normalizedPath = $fullPath.TrimEnd([char[]]"\/")
    $normalizedRoot = $rootPath.TrimEnd([char[]]"\/")
    if (-not $normalizedPath -or $normalizedPath -eq $normalizedRoot) {
        throw "拒绝清理不安全的目录: $fullPath"
    }

    if (Test-Path -LiteralPath $fullPath) {
        Write-Host "清理目录: $fullPath"
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到 Python: $Python"
}
$PythonVersion = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "无法读取 Python 版本: $Python"
}
$PythonVersionParts = $PythonVersion.Trim().Split('.')
if ([int]$PythonVersionParts[0] -lt 3 -or (
    [int]$PythonVersionParts[0] -eq 3 -and [int]$PythonVersionParts[1] -lt 11
)) {
    throw "CEF 桌宠要求 Python 3.11+（当前 $PythonVersion）。请通过 -Python 指定兼容解释器。"
}
if (-not (Test-Path -LiteralPath $ShellProject)) {
    throw "未找到 WinForms 项目: $ShellProject"
}

Get-Process -Name "VirtualCompanion", "VirtualCompanion.Server" -ErrorAction SilentlyContinue |
    Stop-Process -Force

Remove-SafeDirectory -Path $OutputDirectory
Remove-SafeDirectory -Path $BuildRoot
if ($CleanUserData) {
    $expectedUserData = [System.IO.Path]::GetFullPath(
        (Join-Path $env:LOCALAPPDATA "VirtualCompanion")
    )
    $resolvedUserData = [System.IO.Path]::GetFullPath($LocalAppDataPath)
    if ($resolvedUserData -ne $expectedUserData) {
        throw "用户数据目录校验失败: $resolvedUserData"
    }
    Remove-SafeDirectory -Path $resolvedUserData
}

Push-Location $FrontendDir
try {
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Vue 前端构建失败"
    }
}
finally {
    Pop-Location
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $ServerDistRoot `
    --workpath $PyInstallerWork `
    $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller sidecar 打包失败"
}

$FinalDirectory = Join-Path $OutputDirectory "VirtualCompanion"
dotnet publish $ShellProject `
    -c Release `
    -r win-x64 `
    --self-contained true `
    -p:PublishSingleFile=false `
    -p:DebugType=None `
    -p:DebugSymbols=false `
    -o $FinalDirectory
if ($LASTEXITCODE -ne 0) {
    throw "WinForms 外壳发布失败"
}

$ServerSource = Join-Path $ServerDistRoot "VirtualCompanion.Server"
$ServerDestination = Join-Path $FinalDirectory "server"
if (-not (Test-Path -LiteralPath (Join-Path $ServerSource "VirtualCompanion.Server.exe"))) {
    throw "未找到打包后的 sidecar: $ServerSource"
}
Copy-Item -LiteralPath $ServerSource -Destination $ServerDestination -Recurse -Force

# NOTE: keep comments ASCII-only below. This script is UTF-8 without BOM and
# Windows PowerShell 5.1 parses it as ANSI/GBK; a Chinese comment line can
# swallow the next code line and break the build.
# High-res startup icon (loading screen): ship it next to VirtualCompanion.exe.
$IconPngPath = Join-Path $ProjectRoot "ico.png"
if (Test-Path -LiteralPath $IconPngPath) {
    Copy-Item -LiteralPath $IconPngPath -Destination $FinalDirectory -Force
    Write-Host "High-res icon copied: $(Join-Path $FinalDirectory 'ico.png')"
}
else {
    Write-Host "WARNING: ico.png not found in project root; loading screen will fall back to exe icon"
}

if ($CopyProjectEnv) {
    if (-not (Test-Path -LiteralPath $ProjectEnvPath)) {
        throw "未找到项目 .env: $ProjectEnvPath"
    }
    New-Item -ItemType Directory -Path $LocalAppDataPath -Force | Out-Null
    Copy-Item -LiteralPath $ProjectEnvPath -Destination (Join-Path $LocalAppDataPath ".env") -Force
}

$ExecutablePath = Join-Path $FinalDirectory "VirtualCompanion.exe"
Write-Host "打包完成: $ExecutablePath"
Write-Host "Sidecar: $(Join-Path $ServerDestination 'VirtualCompanion.Server.exe')"

if ($RunAfterBuild) {
    Start-Process -FilePath $ExecutablePath
}
