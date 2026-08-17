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
from tkinter import filedialog, messagebox, ttk


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

    def download_dir(self) -> str:
        try:
            lines = self.config.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        in_download = False
        for line in lines:
            if line and not line[0].isspace():
                in_download = line.strip() == "download:"
            elif in_download and line.lstrip().startswith("dir:"):
                return line.split(":", 1)[1].strip().strip('"\'')
        return ""

    def set_download_dir(self, directory: str) -> None:
        folder = Path(directory).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        lines = self.config.read_text(encoding="utf-8").splitlines(keepends=True)
        in_download = False
        for index, line in enumerate(lines):
            if line and not line[0].isspace():
                in_download = line.strip() == "download:"
            elif in_download and line.lstrip().startswith("dir:"):
                indent = line[: len(line) - len(line.lstrip())]
                lines[index] = f"{indent}dir: {json.dumps(str(folder), ensure_ascii=False)}\n"
                self.config.write_text("".join(lines), encoding="utf-8")
                return
        raise RuntimeError("配置文件中没有找到 download.dir。")


def api_json(path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API_URL}{path}", data=data,
        headers={"Content-Type": "application/json"} if data else {}, method=method,
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("code", 0) != 0:
        raise RuntimeError(payload.get("message") or payload.get("msg") or "内核接口返回异常")
    return payload.get("data") or {}


def format_bytes(value: object) -> str:
    try:
        amount = max(0, int(value or 0))
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(amount)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{amount} B"
        size /= 1024
    return "—"


STATUS_TEXT = {0: "等待中", 1: "准备中", 2: "下载中", 3: "已暂停", 4: "合并中", 5: "已完成", 6: "失败", 7: "已取消"}


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.backend = BackendManager()
        self.status = tk.StringVar(value="未启动")
        self.download_dir = tk.StringVar(value=self.backend.download_dir())
        self.task_summary = tk.StringVar(value="启动内核后将自动显示下载任务")
        self.selected_task_text = tk.StringVar(value="请选择一个下载任务")
        self.delete_files = tk.BooleanVar(value=False)
        self._tasks: dict[str, dict] = {}
        self._tasks_loading = False
        self._poll_attempts = 0
        self._closing = False

        self.title("视频号下载工具")
        self.geometry("1000x680")
        self.minsize(820, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.after(250, self.refresh_status)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="视频号下载工具", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(root, text="本地下载管理器 · Go 下载内核在本机后台运行", foreground="#666666").pack(anchor="w", pady=(4, 14))

        status_frame = ttk.LabelFrame(root, text="运行状态", padding=10)
        status_frame.pack(fill="x")
        ttk.Label(status_frame, textvariable=self.status, font=("Microsoft YaHei UI", 12)).pack(anchor="w")

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=(12, 4))
        self.start_button = ttk.Button(controls, text="启动内核", command=self.start_backend)
        self.start_button.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="打开管理页面", command=self.open_admin).pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="安全停止", command=self.stop_backend)
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="查看日志", command=self.open_log).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="数据目录", command=lambda: open_path(self.backend.workdir)).pack(side="left")

        folder = ttk.LabelFrame(root, text="默认下载文件夹（仅影响之后新建的任务）", padding=8)
        folder.pack(fill="x", pady=(8, 10))
        ttk.Entry(folder, textvariable=self.download_dir, state="readonly").grid(row=0, column=0, sticky="ew")
        ttk.Button(folder, text="选择文件夹…", command=self.choose_download_dir).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(folder, text="打开文件夹", command=self.open_download_dir).grid(row=0, column=2, padx=(8, 0))
        folder.columnconfigure(0, weight=1)

        tasks = ttk.LabelFrame(root, text="下载列表", padding=8)
        tasks.pack(fill="both", expand=True)
        toolbar = ttk.Frame(tasks)
        toolbar.pack(fill="x", pady=(0, 7))
        ttk.Label(toolbar, textvariable=self.task_summary).pack(side="left")
        ttk.Button(toolbar, text="刷新", command=self.refresh_tasks).pack(side="right")
        columns = ("name", "progress", "status", "size", "speed", "folder")
        self.task_tree = ttk.Treeview(tasks, columns=columns, show="headings", selectmode="browse")
        for column, heading, width in (
            ("name", "名称", 260), ("progress", "进度", 190), ("status", "状态", 80),
            ("size", "大小", 85), ("speed", "速度", 90), ("folder", "保存位置", 220),
        ):
            self.task_tree.heading(column, text=heading)
            self.task_tree.column(column, width=width, minwidth=60, anchor="w")
        scroll = ttk.Scrollbar(tasks, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=scroll.set)
        self.task_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.task_tree.bind("<<TreeviewSelect>>", self.on_task_selected)

        detail = ttk.Frame(root)
        detail.pack(fill="x", pady=(9, 0))
        ttk.Label(detail, textvariable=self.selected_task_text).grid(row=0, column=0, columnspan=4, sticky="w")
        self.task_progress = ttk.Progressbar(detail, mode="determinate", maximum=100)
        self.task_progress.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 7))
        ttk.Button(detail, text="打开所在文件夹", command=self.open_selected_folder).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(detail, text="同时删除本地文件", variable=self.delete_files).grid(row=2, column=1, padx=(12, 0))
        ttk.Button(detail, text="删除选中任务", command=self.delete_selected_task).grid(row=2, column=2, padx=(12, 0))
        ttk.Button(detail, text="最小化", command=self.iconify).grid(row=2, column=3, sticky="e")
        detail.columnconfigure(0, weight=1)
        ttk.Label(root, text="删除记录不会默认删除视频文件；勾选“同时删除本地文件”才会移除已下载文件。", foreground="#9a6700").pack(anchor="w", pady=(9, 0))

    def choose_download_dir(self) -> None:
        directory = filedialog.askdirectory(title="选择默认下载文件夹", initialdir=self.download_dir.get() or str(Path.home()))
        if not directory:
            return
        try:
            self.backend.prepare()
            self.backend.set_download_dir(directory)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.download_dir.set(str(Path(directory)))
        messagebox.showinfo("已保存", "默认下载文件夹已保存。\n\n请安全停止并重新启动内核后，新建下载任务会使用此文件夹。")

    def open_download_dir(self) -> None:
        directory = self.download_dir.get()
        if not directory:
            messagebox.showwarning("尚未设置", "请先选择默认下载文件夹。")
            return
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)

    def refresh_tasks(self) -> None:
        if self._tasks_loading or not self.backend.is_ready(timeout=0.2):
            return
        self._tasks_loading = True
        def worker() -> None:
            try:
                data = api_json("/api/v1/download_task/list?page=1&page_size=100")
                task_list = data.get("list", [])
                if not isinstance(task_list, list):
                    raise RuntimeError("任务列表格式不正确")
            except Exception as exc:
                self.after(0, lambda: self.task_summary.set(f"读取下载列表失败：{exc}"))
            else:
                self.after(0, lambda: self.render_tasks(task_list, data.get("total", len(task_list))))
            finally:
                self._tasks_loading = False
        threading.Thread(target=worker, daemon=True).start()

    def render_tasks(self, task_list: list[dict], total: object) -> None:
        selected = self.task_tree.selection()
        selected_id = selected[0] if selected else ""
        self._tasks = {str(task.get("id")): task for task in task_list if task.get("id") is not None}
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)
        for task_id, task in self._tasks.items():
            progress = min(100, max(0, float(task.get("progress") or 0)))
            downloaded, size, speed = task.get("downloaded"), task.get("size"), task.get("speed")
            files = task.get("files") or []
            folder = next((str(item.get("download_dir") or "") for item in files if item.get("download_dir")), "")
            self.task_tree.insert("", "end", iid=task_id, values=(
                task.get("name") or "未命名任务", f"{progress:.1f}% · {format_bytes(downloaded)} / {format_bytes(size)}",
                STATUS_TEXT.get(task.get("status"), "未知"), format_bytes(size),
                f"{format_bytes(speed)}/s" if speed else "—", folder,
            ))
        self.task_summary.set(f"共 {total} 个任务 · 每 1.5 秒自动刷新")
        if selected_id in self._tasks:
            self.task_tree.selection_set(selected_id)
        elif self._tasks:
            self.task_tree.selection_set(next(iter(self._tasks)))
        self.on_task_selected()

    def selected_task(self) -> dict | None:
        selected = self.task_tree.selection()
        return self._tasks.get(selected[0]) if selected else None

    def on_task_selected(self, _event=None) -> None:
        task = self.selected_task()
        if not task:
            self.selected_task_text.set("暂无下载任务")
            self.task_progress["value"] = 0
            return
        progress = min(100, max(0, float(task.get("progress") or 0)))
        error = str(task.get("error") or "")
        text = f"{task.get('name') or '未命名任务'} · {STATUS_TEXT.get(task.get('status'), '未知')} · {progress:.1f}%"
        self.selected_task_text.set(f"{text} · {error}" if error else text)
        self.task_progress["value"] = progress

    def open_selected_folder(self) -> None:
        task = self.selected_task()
        if not task:
            messagebox.showwarning("未选择任务", "请先在下载列表中选择一个任务。")
            return
        files = task.get("files") or []
        directory = next((item.get("download_dir") for item in files if item.get("download_dir")), self.download_dir.get())
        if not directory:
            messagebox.showwarning("找不到目录", "该任务没有记录保存位置。")
            return
        path = Path(str(directory)); path.mkdir(parents=True, exist_ok=True); open_path(path)

    def delete_selected_task(self) -> None:
        task = self.selected_task()
        if not task:
            messagebox.showwarning("未选择任务", "请先在下载列表中选择一个任务。")
            return
        delete_files = self.delete_files.get()
        suffix = "并删除本地文件" if delete_files else "，保留本地文件"
        if not messagebox.askyesno("确认删除", f"确定删除“{task.get('name') or task.get('id')}”{suffix}吗？"):
            return
        def worker() -> None:
            try:
                api_json("/api/v1/download_task/delete", "POST", {"task_ids": [int(task["id"])], "delete_files": delete_files})
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("删除失败", str(exc)))
            else:
                self.after(0, self.refresh_tasks)
        threading.Thread(target=worker, daemon=True).start()

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
            self.download_dir.set(self.backend.download_dir())
            self.refresh_tasks()
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
            self.refresh_tasks()
        self.after(1500, self.refresh_status)

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
