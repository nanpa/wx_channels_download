$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CorePath = Join-Path $ProjectDir "core\wx_channels_core.exe"
$GuiVersion = if ($env:WX_CHANNELS_GUI_VERSION) { $env:WX_CHANNELS_GUI_VERSION } else { "0.94-dev" }
$VersionModule = Join-Path $ProjectDir "gui_build_version.py"

if (-not (Test-Path $CorePath)) {
    throw "Missing Go core: $CorePath. Download the Windows core or run the GitHub Actions workflow first."
}

Set-Content -Path $VersionModule -Encoding utf8 -NoNewline -Value "GUI_VERSION = '$GuiVersion'"

Push-Location $ProjectDir
try {
    uvx --python 3.12 --from pyinstaller==6.21.0 pyinstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onedir `
        --name WxChannelsDownload `
        --add-binary "core\wx_channels_core.exe;core" `
        --add-data "default-config.yaml;." `
        --add-data "..\LICENSE;." `
        main.py
}
finally {
    Pop-Location
}

Write-Host "Build completed: $ProjectDir\dist\WxChannelsDownload\WxChannelsDownload.exe"
