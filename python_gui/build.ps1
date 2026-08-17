$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CorePath = Join-Path $ProjectDir "core\wx_channels_core.exe"

if (-not (Test-Path $CorePath)) {
    throw "Missing Go core: $CorePath. Download the Windows core or run the GitHub Actions workflow first."
}

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
        main.py
}
finally {
    Pop-Location
}

Write-Host "Build completed: $ProjectDir\dist\WxChannelsDownload\WxChannelsDownload.exe"
