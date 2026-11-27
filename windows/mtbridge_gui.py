#!/usr/bin/env python3
"""
mtbridge-gui — MiniTavern 本地代理的桌面面板。

标准库 tkinter 实现，零第三方依赖。和 TUI 用同一套 mtbridge.py 后端：
代理线程、账户配置、上游请求全走原有模块，GUI 只负责交互。

设计要点（针对 TUI 按键/鼠标热区「点了没反应」的问题）：
  1. 所有操作都是真正的按钮/菜单，没有字符热区、没有键位映射。
  2. 网络请求一律丢后台线程，主线程只收队列事件更新界面；
     操作期间显示「正在…」并禁用按钮，杜绝重复点击。
  3. 每次点击都在状态栏和日志面板留下反馈，成功失败一眼可见。

运行：
    python mtbridge_gui.py
    python mtbridge_gui.py -c 其他配置.json --port 8787
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import mtbridge  # noqa: E402

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as e:  # pragma: no cover
    print(f"tkinter 不可用：{e}\nWindows Python 安装时请勾选 tcl/tk 组件。", file=sys.stderr)
    sys.exit(2)

FONT = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
MONO = ("Consolas", 9)


def base_dir() -> Path:
    """配置文件的根目录：源码运行时是脚本目录，打包后是 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def system_dark() -> bool:
    """读 Windows 的「应用模式」颜色设置；非 Windows 返回 False。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return int(value) == 0
    except Exception:  # noqa: BLE001
        return False


# clam 是唯一能完整自定义配色的内置 ttk 主题，用它实现亮/暗两套皮肤
THEMES = {
    False: {
        "bg": "#f0f0f0", "fg": "#1a1a1a", "field": "#ffffff",
        "tree_bg": "#ffffff", "tree_fg": "#1a1a1a",
        "select": "#cce4ff", "btn": "#e4e4e4", "btn_active": "#d4d4d4",
        "disabled": "#909090", "log_bg": "#ffffff", "log_fg": "#202020",
    },
    True: {
        "bg": "#202020", "fg": "#e6e6e6", "field": "#2b2b2b",
        "tree_bg": "#2b2b2b", "tree_fg": "#e6e6e6",
        "select": "#3d5a80", "btn": "#333333", "btn_active": "#454545",
        "disabled": "#777777", "log_bg": "#1b1b1b", "log_fg": "#d4d4d4",
    },
}


def short(s: str, n: int = 16) -> str:
    s = s or ""
    return s[:n] + "…" if len(s) > n else s


class AccountDialog(tk.Toplevel):
    """添加 / 编辑账户的模态对话框。self.result 为 None 表示取消。"""

    def __init__(self, parent: tk.Tk, account: dict | None = None):
        super().__init__(parent)
        self.title("编辑账户" if account else "添加账户")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.result: dict | None = None

        frm = ttk.Frame(self, padding=14)
        frm.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        acc = account or {}
        self.label_var = tk.StringVar(value=acc.get("label", ""))
        self.uuid_var = tk.StringVar(value=acc.get("uuid", ""))
        self.client_var = tk.StringVar(value=acc.get("clientId", mtbridge.DEFAULT_CLIENT_ID))
        self.note_var = tk.StringVar(value=acc.get("note", ""))
        self.enabled_var = tk.BooleanVar(value=bool(acc.get("enabled", True)))

        rows = (
            ("备注名", self.label_var),
            ("uuid", self.uuid_var),
            ("clientId", self.client_var),
            ("备注", self.note_var),
        )
        for i, (name, var) in enumerate(rows):
            ttk.Label(frm, text=name).grid(row=i, column=0, sticky="w", pady=5, padx=(0, 10))
            entry = ttk.Entry(frm, textvariable=var, width=46)
            entry.grid(row=i, column=1, sticky="ew", pady=5)
            if name == "uuid":
                self.uuid_entry = entry

        ttk.Checkbutton(frm, text="启用该账户", variable=self.enabled_var).grid(
            row=4, column=1, sticky="w", pady=(6, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btns, text="保存", command=self._ok).pack(side=tk.RIGHT)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.uuid_entry.focus_set()

    def _ok(self) -> None:
        uuid = self.uuid_var.get().strip()
        if len(uuid) < 16:
            messagebox.showerror("输入错误", "uuid 至少 16 位十六进制字符", parent=self)
            return
        self.result = {
            "label": self.label_var.get().strip() or f"账户 {uuid[:6]}",
            "uuid": uuid,
            "clientId": self.client_var.get().strip() or mtbridge.DEFAULT_CLIENT_ID,
            "note": self.note_var.get().strip(),
            "enabled": bool(self.enabled_var.get()),
        }
        self.destroy()


class App:
    def __init__(self, root: tk.Tk, cfg: mtbridge.Config, port: int):
        self.root = root
        self.cfg = cfg
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.q: queue.Queue = queue.Queue()
        self.models: list[dict] = []
        self.busy = False
        self.action_buttons: list[ttk.Button] = []
        # 调试账户入口隐藏：连点账户标题 5 次才出现
        self.debug_unlocked = False
        self._title_taps = 0
        self._last_tap = 0.0

        self._build_ui()
        self._install_hooks()
        self._refresh_all()
        self._update_proxy_buttons()
        # 初始分割：左侧留足按钮行，右侧给模型名更多空间
        root.after(200, lambda: self.mid.sashpos(0, int(root.winfo_width() * 0.42)))
        self._log(f"配置已加载：{cfg.path}（账户 {len(cfg.accounts)} 个）")
        root.after(100, self._drain)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = self.root
        root.title("MiniTavern Bridge · GUI")
        root.geometry("1280x860")
        root.minsize(980, 660)

        st = ttk.Style()
        st.configure("TButton", font=FONT, padding=4)
        st.configure("TLabel", font=FONT)
        st.configure("TEntry", font=FONT)
        st.configure("Treeview", font=FONT, rowheight=24)
        st.configure("Treeview.Heading", font=FONT)

        top = ttk.Frame(root, padding=(10, 8))
        top.pack(fill=tk.X)

        row1 = ttk.Frame(top)
        row1.pack(fill=tk.X)
        self.lbl_proxy = ttk.Label(row1, text="代理未启动", foreground="#888")
        self.lbl_proxy.pack(side=tk.LEFT)
        ttk.Label(row1, text="  端口 ").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(self.port))
        ttk.Entry(row1, textvariable=self.port_var, width=7).pack(side=tk.LEFT)
        self.btn_start = ttk.Button(row1, text="启动代理", command=self.start_proxy)
        self.btn_start.pack(side=tk.LEFT, padx=(8, 3))
        self.btn_stop = ttk.Button(row1, text="停止代理", command=self.stop_proxy)
        self.btn_stop.pack(side=tk.LEFT, padx=3)
        self.lbl_addr = ttk.Label(row1, text="", foreground="#0a7")
        self.lbl_addr.pack(side=tk.LEFT, padx=(12, 3))
        ttk.Button(row1, text="复制地址", command=self.copy_addr).pack(side=tk.LEFT)

        row2 = ttk.Frame(top)
        row2.pack(fill=tk.X, pady=(8, 0))
        self.lbl_active = ttk.Label(row2, text="活动账户：—")
        self.lbl_active.pack(side=tk.LEFT)
        self.lbl_acc_count = ttk.Label(row2, text="")
        self.lbl_acc_count.pack(side=tk.LEFT, padx=16)
        self.lbl_model_count = ttk.Label(row2, text="")
        self.lbl_model_count.pack(side=tk.LEFT, padx=16)

        mid = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        self.mid = mid

        # ---- 账户面板：用 grid 固定「表格 / 按钮行」的上下位置，
        # 表格不会再因为 expand 把按钮挤出面板。
        left = ttk.Frame(mid)
        mid.add(left, weight=3)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        head = ttk.Frame(left)
        head.grid(row=0, column=0, sticky="ew")
        title = ttk.Label(head, text="账户", font=FONT_BOLD)
        title.pack(side=tk.LEFT)
        title.bind("<Button-1>", self._on_title_click)

        tree_box = ttk.Frame(left)
        tree_box.grid(row=1, column=0, sticky="nsew")
        cols = ("state", "label", "uuid", "client", "quota", "note")
        self.acc_tree = ttk.Treeview(tree_box, columns=cols, show="headings",
                                     selectmode="browse", height=8)
        for key, text, width in (
            ("state", "状态", 52), ("label", "备注名", 110), ("uuid", "UUID", 120),
            ("client", "clientId", 96), ("quota", "配额", 76), ("note", "备注", 130),
        ):
            self.acc_tree.heading(key, text=text)
            self.acc_tree.column(key, width=width, anchor=tk.W)
        sb = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=self.acc_tree.yview)
        self.acc_tree.configure(yscrollcommand=sb.set)
        self.acc_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.acc_tree.bind("<Double-1>", lambda _e: self.set_active())
        self.acc_tree.bind("<Button-3>", self._acc_menu)

        # grid 均分列宽：窄窗口下按钮只会变窄，不会被挤出面板
        ops1 = ttk.Frame(left)
        ops1.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for i, (text, cmd) in enumerate((
            ("设为活动", self.set_active),
            ("编辑", self.edit_account),
            ("删除", self.delete_account),
            ("启停", self.toggle_account),
            ("复制UUID", self.copy_uuid),
        )):
            ops1.columnconfigure(i, weight=1)
            b = ttk.Button(ops1, text=text, command=cmd)
            b.grid(row=0, column=i, sticky="ew", padx=(0, 3))
            self.action_buttons.append(b)

        ops2 = ttk.Frame(left)
        ops2.grid(row=3, column=0, sticky="ew", pady=(5, 0))
        for i, (text, cmd) in enumerate((
            ("添加", self.add_account),
            ("导入…", self.import_accounts),
            ("导出…", self.export_accounts),
        )):
            ops2.columnconfigure(i, weight=1)
            b = ttk.Button(ops2, text=text, command=cmd)
            b.grid(row=0, column=i, sticky="ew", padx=(0, 3))
            self.action_buttons.append(b)
        # 调试按钮默认隐藏；连点标题「账户」5 次后才 grid 出来
        ops2.columnconfigure(3, weight=1)
        self.btn_debug = ttk.Button(ops2, text="调试账户",
                                    command=self.new_test_account)

        # ---- 模型面板：倍率放第二列，保证窄窗口下也一定可见；
        # 模型名 stretch 吃掉剩余宽度。
        right = ttk.Frame(mid)
        mid.add(right, weight=4)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        mhead = ttk.Frame(right)
        mhead.grid(row=0, column=0, sticky="ew")
        ttk.Label(mhead, text="可用模型（单击 ID / 名称即复制）",
                  font=FONT_BOLD).pack(side=tk.LEFT)

        mtree_box = ttk.Frame(right)
        mtree_box.grid(row=1, column=0, sticky="nsew")
        mcols = ("id", "rate", "name")
        self.model_tree = ttk.Treeview(mtree_box, columns=mcols, show="headings", height=10)
        for key, text, width in (("id", "模型 ID", 120), ("rate", "倍率", 56),
                                 ("name", "模型名", 220)):
            self.model_tree.heading(key, text=text)
            self.model_tree.column(key, width=width, anchor=tk.W,
                                   stretch=(key == "name"))
        msb = ttk.Scrollbar(mtree_box, orient=tk.VERTICAL,
                            command=self.model_tree.yview)
        self.model_tree.configure(yscrollcommand=msb.set)
        self.model_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        msb.pack(side=tk.RIGHT, fill=tk.Y)
        self.model_tree.bind("<Button-1>", self._on_model_click)

        mops = ttk.Frame(right)
        mops.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        for i, (text, cmd) in enumerate((
            ("刷新模型", self.refresh_models),
            ("检测配额", self.probe_quota),
        )):
            mops.columnconfigure(i, weight=1)
            b = ttk.Button(mops, text=text, command=cmd)
            b.grid(row=0, column=i, sticky="ew", padx=(0, 3))
            self.action_buttons.append(b)

        # ---- 日志 / 状态栏
        logfrm = ttk.LabelFrame(root, text="日志", padding=4)
        logfrm.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 4))
        self.log_text = tk.Text(logfrm, height=5, font=MONO, wrap=tk.NONE,
                                background="#1e1e1e", foreground="#d4d4d4",
                                insertbackground="#d4d4d4")
        lsb = ttk.Scrollbar(logfrm, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.insert(tk.END, "")
        self.log_text.config(state=tk.DISABLED)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.status_var, anchor=tk.W,
                  padding=(10, 4)).pack(fill=tk.X)

        self._dark: bool | None = None
        self._check_theme()

    # ------------------------------------------------------------ 主题
    def _apply_theme(self, dark: bool) -> None:
        c = THEMES[dark]
        st = ttk.Style()
        st.theme_use("clam")
        st.configure(
            ".", background=c["bg"], foreground=c["fg"],
            fieldbackground=c["field"], troughcolor=c["bg"],
            selectbackground=c["select"], selectforeground=c["fg"])
        st.configure("TFrame", background=c["bg"])
        st.configure("TLabel", background=c["bg"], foreground=c["fg"])
        st.configure("TLabelframe", background=c["bg"], foreground=c["fg"])
        st.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])
        st.configure("TPanedwindow", background=c["bg"])
        st.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"],
                     insertcolor=c["fg"])
        st.configure("TButton", background=c["btn"], foreground=c["fg"],
                     bordercolor=c["bg"], lightcolor=c["btn"], darkcolor=c["btn"])
        st.map("TButton", background=[
            ("active", c["btn_active"]), ("disabled", c["bg"])])
        st.configure("TCheckbutton", background=c["bg"], foreground=c["fg"])
        st.configure("Treeview", background=c["tree_bg"],
                     fieldbackground=c["tree_bg"], foreground=c["tree_fg"])
        st.configure("Treeview.Heading", background=c["btn"], foreground=c["fg"])
        self.root.configure(bg=c["bg"])
        self.log_text.configure(background=c["log_bg"], foreground=c["log_fg"],
                                insertbackground=c["fg"])
        self.acc_tree.tag_configure("disabled", foreground=c["disabled"])

    def _check_theme(self) -> None:
        """每 2 秒看一下系统主题，切换暗色/亮色模式时自动跟随。"""
        dark = system_dark()
        if dark != self._dark:
            self._dark = dark
            self._apply_theme(dark)
            self._log(f"界面主题已切换为{'暗色' if dark else '亮色'}")
        self.root.after(2000, self._check_theme)

    # ------------------------------------------------------------ 事件循环
    def _install_hooks(self) -> None:
        """把后端的日志/配额解析接进 GUI 队列。"""
        mtbridge.log = self._backend_log

        orig_parse = mtbridge.parse_quota

        def hooked(text: str):
            q = orig_parse(text)
            if q:
                acc = self.cfg.active_account()
                if acc:
                    total = q.get("total", 0)
                    used = q.get("used", 0)
                    if acc.get("quotaTotal") != total or acc.get("quotaUsed") != used:
                        acc["quotaTotal"] = total
                        acc["quotaUsed"] = used
                        self.q.put(("refresh", None))
            return q

        mtbridge.parse_quota = hooked

    def _backend_log(self, msg: str, *, level: str = "INFO") -> None:
        # pythonw / --windowed 下 sys.stdout 可能是 None，print 会抛异常
        try:
            if sys.stdout:
                print(f"[{level}] {msg}", flush=True)
        except Exception:  # noqa: BLE001
            pass
        self.q.put(("log", f"[{level}] {msg}"))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "refresh":
                    self._refresh_all()
                elif kind == "busy":
                    self._set_busy(bool(payload))
                elif kind == "models":
                    self.models = payload or []
                    self._refresh_all()
                elif kind == "proxy":
                    self._update_proxy_buttons()
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _append_log(self, text: str) -> None:
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {text}\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _log(self, text: str) -> None:
        self.q.put(("log", text))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for b in self.action_buttons:
            b.config(state=state)
        self._update_proxy_buttons()

    def _update_proxy_buttons(self) -> None:
        running = self.httpd is not None
        self.lbl_proxy.config(
            text="代理运行中" if running else "代理未启动",
            foreground="#0a7" if running else "#888")
        self.btn_start.config(state=tk.DISABLED if running or self.busy else tk.NORMAL)
        self.btn_stop.config(state=tk.NORMAL if running and not self.busy else tk.DISABLED)
        self.port_var.set(str(self.port))

    def _refresh_all(self) -> None:
        self.cfg.refresh_if_changed()
        acc = self.cfg.active_account()
        self.lbl_active.config(text=f"活动账户：{acc.get('label') if acc else '—'}")
        self.lbl_acc_count.config(text=f"账户 {len(self.cfg.accounts)} 个")
        self.lbl_model_count.config(text=f"模型 {len(self.models)} 个")

        self.acc_tree.delete(*self.acc_tree.get_children())
        for i, a in enumerate(self.cfg.accounts):
            is_active = acc is not None and a.get("label") == acc.get("label")
            state = "● 活动" if is_active else ("停用" if not a.get("enabled", True) else "")
            tags: list[str] = []
            if not a.get("enabled", True):
                tags.append("disabled")
            self.acc_tree.insert("", tk.END, iid=str(i), values=(
                state,
                a.get("label", ""),
                short(a.get("uuid", "")),
                short(a.get("clientId", ""), 12),
                f"{a.get('quotaUsed', 0)}/{a.get('quotaTotal', 0)}",
                a.get("note", ""),
            ), tags=tags)

        self.model_tree.delete(*self.model_tree.get_children())
        for m in self.models:
            self.model_tree.insert("", tk.END, values=(
                m.get("id", ""), f"{m.get('consumeRate', 1)}x",
                m.get("name", ""),
            ))

    # -------------------------------------------------------------- 工具
    def _selected_account(self) -> tuple[int, dict] | tuple[None, None]:
        sel = self.acc_tree.selection()
        if not sel:
            return None, None
        idx = int(sel[0])
        if idx >= len(self.cfg.accounts):
            return None, None
        return idx, self.cfg.accounts[idx]

    def _need_account(self) -> dict | None:
        acc = self._selected_account()[1]
        if acc is None:
            self.status_var.set("请先选择一个账户")
        return acc

    def _run_task(self, name: str, fn) -> None:
        """后台执行 fn()，返回值作为状态栏消息。"""
        def worker():
            self.q.put(("busy", True))
            self.q.put(("status", f"正在{name}…"))
            self._log(f"开始：{name}")
            try:
                msg = fn() or f"{name}完成"
            except Exception as e:  # noqa: BLE001
                self.q.put(("status", f"{name}失败：{e}"))
                self._log(f"{name}失败：{e}")
            else:
                self.q.put(("status", str(msg)))
                self._log(f"完成：{name} — {msg}")
                self.q.put(("refresh", None))
            finally:
                self.q.put(("busy", False))
        threading.Thread(target=worker, daemon=True, name=f"gui-{name}").start()

    # ---------------------------------------------------------- 代理控制
    def start_proxy(self) -> None:
        try:
            port = int(self.port_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError("端口需在 1–65535 之间")
        except ValueError as e:
            messagebox.showerror("端口错误", str(e))
            return
        try:
            httpd = ThreadingHTTPServer((self.cfg.host, port), mtbridge.Handler)
        except OSError as e:
            messagebox.showerror("启动失败", f"无法绑定 {self.cfg.host}:{port}\n{e}")
            return

        self.httpd = httpd
        self.port = port
        mtbridge.STATE.cfg = self.cfg
        mtbridge.STATE.upstream = mtbridge.Upstream(self.cfg)
        self.server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self.server_thread.start()
        addr = f"http://{self.cfg.host}:{port}/v1"
        self.lbl_addr.config(text=addr)
        self.status_var.set(f"代理已启动 {addr}")
        self._log(f"代理已启动 {addr}")
        self._update_proxy_buttons()

    def stop_proxy(self) -> None:
        httpd = self.httpd
        if httpd is None:
            return
        self.httpd = None
        self.lbl_addr.config(text="")
        self._update_proxy_buttons()

        def worker():
            httpd.shutdown()
            httpd.server_close()
            self.q.put(("status", "代理已停止"))
            self._log("代理已停止")

        threading.Thread(target=worker, daemon=True).start()

    def copy_addr(self) -> None:
        text = self.lbl_addr.cget("text")
        if not text:
            self.status_var.set("代理尚未启动")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"已复制地址 {text}")

    # ---------------------------------------------------------- 账户操作
    def set_active(self) -> None:
        acc = self._need_account()
        if acc is None:
            return
        label = acc.get("label", "")
        self.cfg.save_active(label)
        self._refresh_all()
        self.status_var.set(f"已切换到 {label}")
        self._log(f"切换活动账户 -> {label}")

    def add_account(self) -> None:
        dlg = AccountDialog(self.root)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self.cfg.accounts.append(dlg.result)
        # 添加就是想用它：直接设为活动账户，避免客户端仍走旧账户
        self.cfg.save_active(dlg.result["label"])
        self._refresh_all()
        self.status_var.set(f"已添加并切换为活动账户 {dlg.result['label']}")
        self._log(f"添加账户 {dlg.result['label']} 并设为活动")

    def edit_account(self) -> None:
        idx, acc = self._selected_account()
        if acc is None:
            self.status_var.set("请先选择一个账户")
            return
        dlg = AccountDialog(self.root, acc)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        merged = dict(acc)
        merged.update(dlg.result)
        self.cfg.accounts[idx] = merged
        self.cfg.save()
        self._refresh_all()
        self.status_var.set(f"已保存 {merged['label']}")
        self._log(f"编辑账户 {merged['label']}")

    def delete_account(self) -> None:
        idx, acc = self._selected_account()
        if acc is None:
            self.status_var.set("请先选择一个账户")
            return
        if not messagebox.askyesno("删除账户",
                                   f"确定删除「{acc.get('label')}」？此操作不可撤销。"):
            return
        was_active = acc.get("label") == self.cfg.active_label
        self.cfg.accounts.pop(idx)
        if was_active and self.cfg.accounts:
            self.cfg.active_label = self.cfg.accounts[0].get("label", "")
        self.cfg.save()
        self._refresh_all()
        self.status_var.set(f"已删除 {acc.get('label')}")
        self._log(f"删除账户 {acc.get('label')}")

    def toggle_account(self) -> None:
        idx, acc = self._selected_account()
        if acc is None:
            self.status_var.set("请先选择一个账户")
            return
        new = dict(acc)
        new["enabled"] = not new.get("enabled", True)
        self.cfg.accounts[idx] = new
        self.cfg.save()
        self._refresh_all()
        cur = self.cfg.active_account()
        who = cur.get("label") if cur else "无"
        msg = f"{new['label']} 已{'启用' if new['enabled'] else '停用'}；当前活动：{who}"
        self.status_var.set(msg)
        self._log(msg)

    def copy_uuid(self) -> None:
        acc = self._need_account()
        if acc is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(acc["uuid"])
        self.status_var.set(f"已复制 UUID：{short(acc['uuid'], 12)}")

    # ------------------------------------------------------ 导入 / 导出
    def import_accounts(self) -> None:
        path = filedialog.askopenfilename(
            title="导入账户列表",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            messagebox.showerror("读取失败", str(e))
            return

        def task():
            added, skipped = self.cfg.import_json(raw, replace=False)
            active = self.cfg.active_account()
            tip = f"当前活动：{active.get('label') if active else '无'}"
            if added:
                tip += "；要用新账户请点「设为活动」"
            return f"导入完成：新增 {added}，跳过重复 {skipped}。{tip}"

        self._run_task("导入账户", task)

    def export_accounts(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出账户列表", defaultextension=".json",
            initialfile="minitavern-accounts.json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            Path(path).write_text(self.cfg.export_json(), encoding="utf-8")
        except OSError as e:
            messagebox.showerror("写入失败", str(e))
            return
        self.status_var.set(f"已导出 {len(self.cfg.accounts)} 个账户 -> {path}")
        self._log(f"导出账户列表 -> {path}")

    def new_test_account(self) -> None:
        def task():
            acc = mtbridge.add_test_account(self.cfg)
            return f"已创建 {acc.get('label')}（uuid {short(acc.get('uuid', ''), 12)}…）"

        self._run_task("开户", task)

    # ------------------------------------------------------ 模型 / 配额
    def refresh_models(self) -> None:
        acc = self.cfg.active_account()
        if acc is None:
            self.status_var.set("没有活动账户")
            return
        cid = acc.get("clientId", "")

        def task():
            err, models = mtbridge.Upstream(self.cfg).models(cid, force=True)
            if err != "ok":
                raise RuntimeError(err)
            self.q.put(("models", models))
            return f"模型目录已更新：{len(models)} 个"

        self._run_task("刷新模型", task)

    def probe_quota(self) -> None:
        acc = self.cfg.active_account()
        if acc is None:
            self.status_var.set("没有活动账户")
            return
        if not messagebox.askokcancel(
                "检测配额",
                "检测会真实发送一次请求，消耗 1 点配额。继续？"):
            return

        def task():
            up = mtbridge.Upstream(self.cfg)
            _, models = up.models(acc.get("clientId", ""))
            model = models[0]["name"] if models else "deepseek/deepseek-v3.2-exp"
            q = up.probe_quota(acc, model)
            if not q:
                raise RuntimeError("响应中没有 otherInfo")
            return f"配额 {q.get('used', 0)}/{q.get('total', 0)}（本次消耗 1 点）"

        self._run_task("检测配额", task)

    def _on_model_click(self, event: tk.Event) -> None:
        row = self.model_tree.identify_row(event.y)
        if not row:
            return
        col = self.model_tree.identify_column(event.x)
        values = self.model_tree.item(row, "values")
        if col == "#1":
            text, label = values[0], "模型 ID"
        elif col == "#3":
            text, label = values[2], "模型名"
        else:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"已复制{label}：{text}")

    def _on_title_click(self, _event: tk.Event) -> None:
        """连点账户标题 5 次（3 秒内）解锁隐藏的调试账户按钮。"""
        now = time.time()
        self._title_taps = self._title_taps + 1 if now - self._last_tap < 3.0 else 1
        self._last_tap = now
        if self._title_taps < 5:
            return
        self._title_taps = 0
        if self.debug_unlocked:
            return
        self.debug_unlocked = True
        self.btn_debug.grid(row=0, column=3, sticky="ew", padx=(0, 3))
        self.action_buttons.append(self.btn_debug)
        self._set_busy(self.busy)
        self.status_var.set("调试功能已解锁")
        self._log("调试功能已解锁")

    def _acc_menu(self, event: tk.Event) -> None:
        iid = self.acc_tree.identify_row(event.y)
        if iid:
            self.acc_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="设为活动", command=self.set_active)
        menu.add_command(label="复制 UUID", command=self.copy_uuid)
        menu.add_separator()
        menu.add_command(label="编辑", command=self.edit_account)
        menu.add_command(label="删除", command=self.delete_account)
        menu.tk_popup(event.x_root, event.y_root)

    def on_close(self) -> None:
        if self.httpd is not None:
            httpd = self.httpd
            self.httpd = None
            httpd.shutdown()
            httpd.server_close()
        self.root.destroy()


def main() -> int:
    ap = argparse.ArgumentParser(prog="mtbridge-gui",
                                 description="MiniTavern 本地代理 · 桌面面板")
    ap.add_argument("-c", "--config", type=Path,
                    default=base_dir() / "config.json")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    cfg = mtbridge.load_config(args.config)
    errs = cfg.problems()
    if errs:
        msg = "配置错误：\n  " + "\n  ".join(errs)
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("配置错误", msg)
        root.destroy()
        return 2

    root = tk.Tk()
    App(root, cfg, args.port or cfg.port)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
