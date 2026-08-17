# Tkinter Windows 启动器

这个启动器只使用 Python 标准库，不依赖 PySide6。它负责启动预编译的 Go 内核、打开 Web 管理页面、查看日志和安全停止服务。

## 本地运行（不安装 Go）

1. 在 GitHub 仓库打开 `Actions` → `Windows Tkinter bundle` → `Run workflow`。
2. 等待任务完成，下载 `wx_channels_core-windows-x86_64` artifact。
3. 解压得到 `wx_channels_core.exe`。
4. 放到 `python_gui/core/wx_channels_core.exe`。
5. 在 Windows PowerShell 运行：

```powershell
cd python_gui
uv run --python 3.12 python main.py
```

也可通过环境变量指定另一个内核位置：

```powershell
$env:WX_CHANNELS_CORE_PATH = "C:\path\to\wx_channels_core.exe"
uv run --python 3.12 python main.py
```

## 打包

```powershell
cd python_gui
.\build.ps1
```

产物位于：

```text
python_gui/dist/WxChannelsDownload/WxChannelsDownload.exe
```

`dist/WxChannelsDownload` 是一个整体，不能只复制最外层 EXE。

## 安全说明

`default-config.yaml` 默认关闭代理和根证书安装，仅供 GUI 开发与调试。在每台 Windows 机器独立生成根证书、记录指纹并实现精确卸载前，不要将抓取功能作为正式版分发。
