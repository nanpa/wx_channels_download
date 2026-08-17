---
title: GUI 模式
---

# GUI 命令

`gui` 命令将现有的 API、下载引擎、请求代理和 Web 管理界面组合为一个图形化应用入口。

## 启动

```bash
go run . gui
```

服务就绪后，程序会：

1. 打开本地管理页面。
2. 在 macOS 或 Windows 的系统托盘显示图标。
3. 在托盘菜单提供「打开页面」、「设置/取消系统代理」和「退出」。

关闭浏览器页面不会停止程序，正在进行的下载会继续运行。退出时会先停止代理并还原系统网络设置，再停止 API 和下载引擎。

Linux 会正常打开管理页面，但当前托盘依赖未提供 Linux 实现，需在终端使用 `Ctrl+C` 退出。

## 参数

```text
--no-open   启动后不自动打开管理界面
```

`gui` 同样支持根命令的通用参数，例如 `--config`、`--workdir`、`--hostname` 和 `--port`。

```bash
go run . gui --port 2024 --workdir ./runtime
```
