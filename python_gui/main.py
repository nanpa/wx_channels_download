from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


APP_NAME = "WxChannelsDownload"
API_URL = "http://127.0.0.1:2022"
HEALTH_URL = f"{API_URL}/api/status"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def resource_dir() -> Path:
    bundled_dir = getattr(sys, "_MEIPASS", None)
    return Path(bundled_dir) if bundled_dir else Path(__file__).resolve().parent


def user_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class BackendManager:
    def __init__(self) -> None:
        self.assets = resource_dir()
        self.workdir = user_data_dir()
        configured_core = os.environ.get("WX_CHANNELS_CORE_PATH")
        self.core = (
            Path(configured_core).expanduser().resolve()
            if configured_core
            else self.assets / "core" / "wx_channels_core.exe"
        )
        self.default_config = self.assets / "default-config.yaml"
        self.config = self.workdir / "config.yaml"
        self.log = self.workdir / "core.log"
        self.pid_file = self.workdir / "wx_video_download.pid"
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle = None

    def prepare(self) -> None:
        if not self.core.is_file():
            raise FileNotFoundError(
                f"没有找到 Go 内核：\n{self.core}\n\n"
                "请将 Windows 内核重命名为 wx_channels_core.exe，"
                "放入 python_gui\\core 目录。"
            )
        if not self.config.exists():
            if not self.default_config.is_file():
                raise FileNotFoundError(f"缺少默认配置：{self.default_config}")
            shutil.copy2(self.default_config, self.config)

    def is_ready(self, timeout: float = 0.7) -> bool:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def start(self) -> None:
        self.prepare()
        if self.is_ready():
            return
        self._clear_stale_pid_file()

        self._log_handle = self.log.open("ab")
        command = [
            str(self.core),
            "--workdir",
            str(self.workdir),
            "--config",
            str(self.config),
            "server",
        ]
        try:
            self._process = subprocess.Popen(
                command,
                cwd=self.workdir,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            self._close_log()
            raise

    def stop(self) -> None:
        if not self.core.is_file():
            return

        # Stop the interceptor first so Windows' system proxy is restored before
        # the server process is terminated by the existing CLI stop command.
        if self.is_ready():
            body = json.dumps({"name": "proxy"}).encode("utf-8")
            request = urllib.request.Request(
                f"{API_URL}/api/service/stop",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    if response.status != 200:
                        raise RuntimeError(f"停止代理失败，HTTP {response.status}")
            except Exception as exc:
                raise RuntimeError(
                    "无法先恢复 Windows 系统代理，已取消强制结束内核。\n"
                    f"详细错误：{exc}"
                ) from exc

        if self._process is not None and self._process.poll() is None:
            # This is the exact child launched by this GUI, so it cannot target
            # an unrelated process whose PID Windows later reused.
            self._process.terminate()
            try:
                self._process.wait(timeout=8)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("内核停止超时，请稍后重试。") from exc
        else:
            pid = self._read_pid_file()
            if pid is not None and self._pid_matches_core(pid):
                command = [
                    str(self.core),
                    "--workdir",
                    str(self.workdir),
                    "--config",
                    str(self.config),
                    "server",
                    "stop",
                ]
                result = subprocess.run(
                    command,
                    cwd=self.workdir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=12,
                    check=False,
                    creationflags=CREATE_NO_WINDOW,
                )
                if result.returncode != 0:
                    detail = result.stdout.decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"内核停止失败。{detail}")

        deadline = time.monotonic() + 8
        while self.is_ready(timeout=0.25) and time.monotonic() < deadline:
            time.sleep(0.2)
        if self.is_ready(timeout=0.25):
            raise RuntimeError("内核仍在运行，未清理 PID 文件。")
        self.pid_file.unlink(missing_ok=True)
        self._process = None
        self._close_log()

    def _read_pid_file(self) -> int | None:
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _pid_matches_core(self, pid: int) -> bool:
        if pid <= 0 or os.name != "nt":
            return False
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        return f'"{self.core.name.lower()}"' in result.stdout.lower()

    def _clear_stale_pid_file(self) -> None:
        pid = self._read_pid_file()
        if pid is None:
            return
        if self._pid_matches_core(pid):
            raise RuntimeError(
                f"检测到内核进程仍在运行（PID: {pid}），但管理服务未就绪。"
                "请先在任务管理器结束 wx_channels_core.exe 后重试。"
            )
        self.pid_file.unlink(missing_ok=True)

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.backend = BackendManager()
        self.status = tk.StringVar(value="未启动")
        self._poll_attempts = 0
        self._closing = False

        self.title("视频号下载工具")
        self.geometry("620x390")
        self.minsize(560, 350)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.after(250, self.refresh_status)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=24)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="视频号下载工具", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text="Tkinter 启动器 · Go 内核在本机后台运行",
            foreground="#666666",
        ).pack(anchor="w", pady=(4, 22))

        status_frame = ttk.LabelFrame(root, text="运行状态", padding=16)
        status_frame.pack(fill="x")
        ttk.Label(status_frame, textvariable=self.status, font=("Microsoft YaHei UI", 12)).pack(anchor="w")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=20)
        self.start_button = ttk.Button(buttons, text="启动内核", command=self.start_backend)
        self.start_button.grid(row=0, column=0, padx=(0, 10), pady=5, sticky="ew")
        ttk.Button(buttons, text="打开管理页面", command=self.open_admin).grid(
            row=0, column=1, padx=(0, 10), pady=5, sticky="ew"
        )
        self.stop_button = ttk.Button(buttons, text="安全停止", command=self.stop_backend)
        self.stop_button.grid(row=0, column=2, pady=5, sticky="ew")
        ttk.Button(buttons, text="打开数据目录", command=lambda: open_path(self.backend.workdir)).grid(
            row=1, column=0, padx=(0, 10), pady=5, sticky="ew"
        )
        ttk.Button(buttons, text="查看日志", command=self.open_log).grid(
            row=1, column=1, padx=(0, 10), pady=5, sticky="ew"
        )
        ttk.Button(buttons, text="最小化", command=self.iconify).grid(row=1, column=2, pady=5, sticky="ew")
        for column in range(3):
            buttons.columnconfigure(column, weight=1)

        warning = (
            "当前默认为安全的 GUI 开发配置：不安装根证书，不修改系统代理。\n"
            "完成每台电脑独立证书的内核加固后，再用于正式抓取和分发。"
        )
        ttk.Label(root, text=warning, foreground="#9a6700", wraplength=550, justify="left").pack(anchor="w")

    def run_async(self, operation, success_message: str) -> None:
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")

        def worker() -> None:
            try:
                operation()
            except Exception as exc:
                error_message = str(exc)
                self.after(0, lambda: messagebox.showerror("操作失败", error_message))
            else:
                self.after(0, lambda: self.status.set(success_message))
            finally:
                self.after(0, lambda: self.start_button.configure(state="normal"))
                self.after(0, lambda: self.stop_button.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def start_backend(self) -> None:
        self.status.set("正在启动内核……")
        self._poll_attempts = 0
        self.run_async(self.backend.start, "已发出启动请求")
        self.after(500, self.poll_until_ready)

    def poll_until_ready(self) -> None:
        if self.backend.is_ready():
            self.status.set("运行中 · http://127.0.0.1:2022")
            self.open_admin()
            return
        self._poll_attempts += 1
        if self._poll_attempts < 60:
            self.after(500, self.poll_until_ready)
        else:
            self.status.set("启动超时，请查看日志")

    def stop_backend(self) -> None:
        self.status.set("正在安全停止并恢复系统代理……")
        self.run_async(self.backend.stop, "已停止")

    def refresh_status(self) -> None:
        if not self._closing and self.backend.is_ready(timeout=0.25):
            self.status.set("运行中 · http://127.0.0.1:2022")
        self.after(3000, self.refresh_status)

    def open_admin(self) -> None:
        if not self.backend.is_ready():
            messagebox.showwarning("服务未就绪", "请先启动内核，等待状态变为“运行中”。")
            return
        webbrowser.open(API_URL, new=2)

    def open_log(self) -> None:
        self.backend.workdir.mkdir(parents=True, exist_ok=True)
        if not self.backend.log.exists():
            self.backend.log.touch()
        open_path(self.backend.log)

    def on_close(self) -> None:
        if self.backend.is_ready() and messagebox.askyesno(
            "退出程序",
            "退出时是否同时安全停止内核？\n\n"
            "选择“否”将只关闭启动器，后台服务会继续运行。",
        ):
            self._closing = True
            self.withdraw()

            def stop_and_close() -> None:
                try:
                    self.backend.stop()
                except Exception as exc:
                    error_message = str(exc)
                    self.after(0, self.deiconify)
                    self.after(0, lambda: messagebox.showerror("安全停止失败", error_message))
                    self._closing = False
                    return
                self.after(0, self.destroy)

            threading.Thread(target=stop_and_close, daemon=True).start()
            return
        self.destroy()


def main() -> int:
    launcher = Launcher()
    launcher.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
