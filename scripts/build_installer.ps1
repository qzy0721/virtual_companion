[CmdletBinding()]
param(
    [string]$AppVersion = "0.2.0",
    [string]$IsccPath = "",
    [string]$SourceDirectory = "",
    [string]$OutputDirectory = "",
    [switch]$RebuildApplication
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$InstallerScript = Join-Path $ProjectRoot "packaging/virtual_companion.iss"
$ApplicationBuildScript = Join-Path $PSScriptRoot "build_package.ps1"
$SetupIcon = Join-Path $ProjectRoot "web/public/favicon.ico"

if (-not $SourceDirectory) {
    $SourceDirectory = Join-Path $ProjectRoot "dist/VirtualCompanion"
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot "dist/installer"
}

function Resolve-IsccPath {
    param([string]$ConfiguredPath)

    if ($ConfiguredPath) {
        $resolved = [System.IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($ConfiguredPath)
        )
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "未找到 ISCC.exe: $resolved"
        }
        return $resolved
    }

    foreach ($commandName in @("ISCC.exe", "ISCC", "ICSS.exe", "ICSS")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command -and $command.Source) {
            return $command.Source
        }
    }

    $registryKeys = @(
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    $installations = Get-ItemProperty $registryKeys -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like "Inno Setup*" -and $_.InstallLocation }
    foreach ($installation in $installations) {
        $candidate = Join-Path $installation.InstallLocation "ISCC.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    $standardPaths = @(
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $standardPaths) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }

    throw "未找到 ISCC.exe，请将 Inno Setup 加入 PATH 或使用 -IsccPath 指定路径"
}

if ($AppVersion -notmatch '^\d+(\.\d+){1,3}$') {
    throw "AppVersion 必须是 0.1.0 或 1.2.3.4 形式的数字版本"
}
if ($RebuildApplication) {
    & $ApplicationBuildScript
    if ($LASTEXITCODE -ne 0) {
        throw "one-folder 应用构建失败"
    }
}

$SourceDirectory = [System.IO.Path]::GetFullPath($SourceDirectory)
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$requiredFiles = @(
    (Join-Path $SourceDirectory "VirtualCompanion.exe"),
    (Join-Path $SourceDirectory "server/VirtualCompanion.Server.exe"),
    (Join-Path $SourceDirectory "runtimes/win-x64/native/WebView2Loader.dll")
)
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "one-folder 产物不完整，缺少: $requiredFile"
    }
}
if (-not (Test-Path -LiteralPath $InstallerScript -PathType Leaf)) {
    throw "未找到 Inno Setup 脚本: $InstallerScript"
}
if (-not (Test-Path -LiteralPath $SetupIcon -PathType Leaf)) {
    throw "未找到安装器图标: $SetupIcon"
}

$compiler = Resolve-IsccPath -ConfiguredPath $IsccPath
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

Write-Host "Inno Setup: $compiler"
Write-Host "应用目录: $SourceDirectory"
Write-Host "安装器输出: $OutputDirectory"

& $compiler `
    "/Qp" `
    "/DMyAppVersion=$AppVersion" `
    "/DSourceDir=$SourceDirectory" `
    "/DOutputDir=$OutputDirectory" `
    "/DSetupIconFile=$SetupIcon" `
    $InstallerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup 编译失败"
}

$installerPath = Join-Path $OutputDirectory "VirtualCompanion-Setup-$AppVersion.exe"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Inno Setup 未生成预期的安装器: $installerPath"
}

$installer = Get-Item -LiteralPath $installerPath
Write-Host "安装包已生成: $($installer.FullName)"
Write-Host ("安装包大小: {0:N1} MiB" -f ($installer.Length / 1MB))
