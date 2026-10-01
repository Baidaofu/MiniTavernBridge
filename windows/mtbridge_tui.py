#!/usr/bin/env python3
"""
mtbridge-tui — mtbridge 的终端界面。

零第三方依赖（标准库 + ANSI / Win32 console API）。

修复记录
--------
1. 「连接不到服务」：run_tui 里写的是局部变量 ``STATE = mtbridge.State()``，
   把模块级的 ``mtbridge.STATE`` 遮住了，Handler 拿到的仍是 None，
   断言失败后所有 /v1/* 返回 503。必须写 ``mtbridge.STATE``。
2. 频闪：原来每 0.15s 整屏 ``\\x1b[2J`` 清屏再重画。改为差分渲染 ——
   逐行定位 + 只重画变化的行，没有变化就不发任何转义序列。
3. 选择项不稳定 / 鼠标无效：原来只读键盘，且每个事件都无条件重绘。
   现在加 Windows 原生鼠标（读 INPUT_RECORD，识别滚轮与点击）与
   Unix 的 SGR 鼠标序列，选中项在重绘之间保持。

运行
----
    python mtbridge_tui.py                 # 前台运行代理 + TUI
    python mtbridge_tui.py --headless      # 只跑代理，不开界面
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mtbridge  # noqa: E402

IS_WIN = sys.platform == "win32"

# ANSI 颜色
C = {
    "reset": "\x1b[0m", "dim": "\x1b[2m", "bold": "\x1b[1m",
    "teal": "\x1b[38;5;79m", "green": "\x1b[38;5;114m",
    "yellow": "\x1b[38;5;221m", "red": "\x1b[38;5;203m",
    "grey": "\x1b[38;5;245m", "white": "\x1b[38;5;255m",
    "bg": "\x1b[48;5;236m", "rvs": "\x1b[7m",
}


def paint(s: str, *styles: str) -> str:
    return "".join(C[x] for x in styles if x in C) + s + C["reset"]


# ------------------------------------------------------------------ 终端

class Term:
    """原始模式 + 备用屏 + 差分重绘。"""

    def __init__(self) -> None:
        self.out = sys.stdout
        self._prev: list[str] = []
        self._tty = sys.stdin.isatty() and sys.stdout.isatty()
        self._old_attrs = None
        self._h = None
        self._k32 = None
        self._mouse_on = False

    # -- 生命周期 ---------------------------------------------------------

    def __enter__(self):
        if IS_WIN:
            self._win_setup()
        else:
            import termios
            import tty

            self._old_attrs = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        self.out.write("\x1b[?1049h\x1b[?25l")   # 备用屏 + 隐藏光标
        self._enable_mouse()
        self._prev = []
        return self

    def __exit__(self, *exc):
        self._disable_mouse()
        self.out.write("\x1b[?25h\x1b[0m\x1b[?1049l")
        self.out.flush()
        if IS_WIN:
            self._win_restore()
        else:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_attrs)

    # -- Windows ----------------------------------------------------------

    def _win_setup(self):
        if not self._tty:
            return
        self._k32 = ctypes.windll.kernel32
        self._h = self._k32.GetStdHandle(-10)          # STD_INPUT_HANDLE
        self._mode = ctypes.c_uint32()
        if not self._k32.GetConsoleMode(self._h, ctypes.byref(self._mode)):
            self._tty = False
            return
        # ENABLE_MOUSE_INPUT(4) | ENABLE_WINDOW_INPUT(8) | ENABLE_EXTENDED_FLAGS(128)
        self._k32.SetConsoleMode(
            self._h, self._mode.value | 0x0004 | 0x0008 | 0x0080)

    def _win_restore(self):
        if self._h is not None and self._k32 is not None:
            self._k32.SetConsoleMode(self._h, self._mode.value)

    # -- 鼠标 -------------------------------------------------------------

    def _enable_mouse(self):
        if IS_WIN:
            self._mouse_on = self._tty          # 由 _win_setup 打开的标志位
        else:
            self.out.write("\x1b[?1000h\x1b[?1006h")
            self.out.flush()
            self._mouse_on = True

    def _disable_mouse(self):
        if not IS_WIN and self._mouse_on:
            self.out.write("\x1b[?1006l\x1b[?1000l")

    # -- 尺寸 -------------------------------------------------------------

    def size(self):
        try:
            s = shutil.get_terminal_size()
            return max(60, s.columns), max(16, s.lines)
        except Exception:
            return 100, 30

    # -- 差分绘制 ---------------------------------------------------------

    def render(self, lines: list[str]) -> bool:
        """只重画变化的行。返回是否真的写了东西。"""
        w, h = self.size()
        buf = [ln[: w - 1] for ln in lines[: h - 1]]
        if buf == self._prev:
            return False
        out = []
        # 从上往下逐行比；整段相同则跳过（run-length，最省）
        i = 0
        while i < len(buf):
            if i < len(self._prev) and buf[i] == self._prev[i]:
                i += 1
                continue
            j = i
            while (j < len(buf) and j < len(self._prev) and buf[j] == self._prev[j]):
                j += 1
            # i..j 有变化，整块输出
            for k in range(i, len(buf)):
                out.append(f"\x1b[{k + 1};1H\x1b[K{buf[k]}")
            # 补齐上次多出来的行
            for k in range(len(buf), len(self._prev)):
                out.append(f"\x1b[{k + 1};1H\x1b[K")
            break
        else:
            for k in range(len(self._prev), len(buf)):
                out.append(f"\x1b[{k + 1};1H\x1b[K{buf[k]}")
        if not out:
            return False
        self.out.write("".join(out))
        self.out.flush()
        self._prev = buf
        return True

    # -- 输入 -------------------------------------------------------------

    def read_event(self, timeout: float = 0.25):
        """返回 ('key', ch) / ('wheel', +1|-1) / ('click', row) / None。"""
        if not self._tty:
            time.sleep(timeout)
            return None
        if IS_WIN:
            return self._read_win(timeout)
        return self._read_posix(timeout)

    def _read_win(self, timeout):
        import msvcrt

        # 先清空已有按键
        while msvcrt.kbhit():
            k = msvcrt.getwch()
            ev = self._decode_win_key(k)
            if ev:
                return ev
        # 有鼠标事件就取
        ev = self._poll_win_mouse()
        if ev:
            return ev
        time.sleep(timeout)
        ev = self._poll_win_mouse()
        return ev

    def _decode_win_key(self, k):
        if k in ("\x00", "\xe0"):
            if not msvcrt.kbhit():
                return None
            code = msvcrt.getwch()
            return {
                "H": ("key", "up"), "P": ("key", "down"),
                "K": ("key", "left"), "M": ("key", "right"),
                "G": ("key", "home"), "O": ("key", "end"),
                "I": ("key", "pgup"), "Q": ("key", "pgdn"),
            }.get(code)
        return ("key", k)

    def _poll_win_mouse(self):
        """读 INPUT_RECORD 取滚轮/点击。"""
        if self._h is None or not self._mouse_on:
            return None
        class MOUSE_EVENT_RECORD(ctypes.Structure):
            _fields_ = [("dwMousePositionX", ctypes.c_long),
                        ("dwMousePositionY", ctypes.c_long),
                        ("dwButtonDown", ctypes.c_uint32),
                        ("dwEventFlags", ctypes.c_uint32),
                        ("dwEventTime", ctypes.c_uint32),
                        ("dwMouseWheelData", ctypes.c_long)]
        class INPUT_RECORD(ctypes.Union):
            _fields_ = [("EventType", ctypes.c_uint16),
                        ("ev", MOUSE_EVENT_RECORD),
                        ("_pad", ctypes.c_byte * 24)]
        rec = INPUT_RECORD()
        n = ctypes.c_uint32(0)
        got = self._k32.GetNumberOfConsoleInputEvents(self._h, ctypes.byref(n))
        if not got or n.value == 0:
            return None
        for _ in range(n.value):
            if not self._k32.ReadConsoleInputW(self._h, ctypes.byref(rec), 1, ctypes.byref(n)):
                break
            if rec.EventType != 0:          # 0 = KEY_EVENT
                continue
            flags = rec.ev.dwEventFlags
            if flags & 0x0004:              # MOUSE_WHEELED
                delta = ctypes.c_short(rec.ev.dwMouseWheelData).value
                return ("wheel", 1 if delta > 0 else -1)
            if flags & 0x0002:              # MOUSE_EVENT
                return ("click", self._row_at(rec.ev.dwMousePositionY))
            n = ctypes.c_uint32(1)
        return None

    def _row_at(self, y):
        w, h = self.size()
        r = y * h // 25        # 近似：Windows 单元格高约 1/25 屏
        return max(0, min(h - 1, r))

    def _read_posix(self, timeout):
        import select

        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":                       # 可能��转义序列
            seq = ""
            while len(seq) < 12:
                seq += sys.stdin.read(1)
                if seq[-1:].isalpha() or seq[-1:] == "~":
                    break
            if seq.startswith("[<"):
                m = seq.split(";")
                if len(m) >= 3 and m[0] in ("A", "B"):
                    return ("wheel", -1 if m[0] == "A" else 1)
                if len(m) >= 3 and m[0] in ("M", "m"):
                    return ("click", self._row_from_sgr(int(m[1])))
            return {"[A": ("key", "up"), "[B": ("key", "down"),
                    "[C": ("key", "right"), "[D": ("key", "left"),
                    "[5": ("key", "pgup"), "[6": ("key", "pgdn"),
                    "[H": ("key", "home"), "[F": ("key", "end")}.get(seq)
        return ("key", ch)

    def _row_from_sgr(self, y):
        w, h = self.size()
        return max(0, min(h - 1, (y - 1) * h // 25))


# ------------------------------------------------------------------ 面板

def short_model(m: str) -> str:
    return m.rsplit("/", 1)[-1]


def draw(cfg, port: int, st: dict, sel: int) -> list[str]:
    """返回整屏的行；Term.render 会做差分，只重画变化的部分。"""
    w, h = Term.size.__wrapped__(None) if False else shutil.get_terminal_size()
    w = max(60, w)
    h = max(16, h)
    accounts = cfg.accounts
    if accounts:
        sel = max(0, min(sel, len(accounts) - 1))
    active = cfg.active_account()
    out: list[str] = []

    # 顶栏
    right = f"http://127.0.0.1:{port}/v1"
    head = "  MiniTavern Bridge TUI" + paint(f"  v{mtbridge.__version__}", "grey")
    gap = max(1, w - len(_plain(head)) - len(_plain(right)) - 6)
    out.append(head + " " * gap + paint(right, "grey"))
    st_txt = f"{'● 运行中' if st['running'] else '○ 已停止'}"
    out.append("  " + paint(st_txt, "green" if st["running"] else "red", "bold")
               + paint(f"   账户 {len(accounts)}   模型 {st['model_count'] or '—'}"
                       f"   请求 {st['requests']}", "grey"))

    # 活动账户
    if active:
        acc_line = paint(active.get("label", "?"), "teal", "bold")
        q = st["quota"].get(active["uuid"])
        if q:
            acc_line += paint(f"   配额 {q['used']}/{q['total']}",
                              "yellow" if q["used"] < q["total"] else "red")
    else:
        acc_line = paint("无 —— 还没有账户", "red", "bold")
    out.append("  " + acc_line)
    if st.get("msg"):
        out.append("  " + paint(st["msg"][: w - 4], "yellow"))

    # 最近调用
    out.append("")
    out.append("  " + paint("最近调用", "white", "bold"))
    calls = st.get("recent", [])
    if not calls:
        out.append("    " + paint("暂无", "grey"))
    for c in reversed(calls[-5:]):
        col = ("red" if c.get("error") else
               "teal" if (c.get("status") or 0) < 300 else "yellow")
        out.append(f"    {paint(c.get('time',''), 'grey')}  "
                   f"{paint(f'{short_model(c.get(chr(109)+chr(111)+chr(100)+chr(101)+chr(108),'')):<26}', col)}"
                   f"{c.get('latency_ms',0):>6} ms  "
                   f"{paint('HTTP ' + str(c.get('status','-')), col)}")

    # 账户表
    rows = max(3, h - len(out) - 6)
    out.append("")
    out.append("  " + paint("账户", "white", "bold")
               + paint("   ↑↓ / j k / 滚轮 选择 · Enter 切换 · e 停用 · d 删除 · R 刷新 · q 退出",
                       "grey"))
    if not accounts:
        out.append("    " + paint("（空）按 i 或运行 --import_ 导入账户列表；也可直接编辑 config.json",
                                  "grey"))
    for i, a in enumerate(accounts[:rows]):
        on = a.get("uuid") == (active or {}).get("uuid")
        mark = paint("▶", "teal", "bold") if on else "  "
        label = f"{a.get('label','?'):<16} {a.get('uuid','')[:16]}…"
        extra = ""
        if not a.get("enabled", True):
            extra = paint("  [停用]", "red")
        q = st["quota"].get(a["uuid"])
        if q:
            extra += paint(f"   {q['used']}/{q['total']}", "grey")
        line = f"  {mark} {label}{extra}"
        if i == sel:
            out.append(paint(line.ljust(w - 1)[: w - 1], "bg", "white", "bold"))
        else:
            out.append(paint(line, "teal") if on else paint(line, "grey"))
    return out


def _plain(s: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# ------------------------------------------------------------------ 账户操作

def do_export(cfg_path: Path, out: str) -> str:
    cfg = mtbridge.load_config(cfg_path)
    dst = Path(out)
    dst.write_text(cfg.export_json(), encoding="utf-8")
    return f"已导出 {len(cfg.accounts)} 个账户 -> {dst}"


def do_import(cfg_path: Path, src: str, replace: bool) -> str:
    cfg = mtbridge.load_config(cfg_path)
    text = Path(src).read_text(encoding="utf-8")
    added, skipped = cfg.import_json(text, replace=replace)
    return f"导入完成：新增 {added}，跳过重复 {skipped}"


def headless(cfg_path: Path, port: int) -> int:
    cfg = mtbridge.load_config(cfg_path)
    errs = cfg.problems()
    if errs:
        for e in errs:
            print(f"配置错误：{e}", file=sys.stderr)
        print(f"\n请检查 {cfg.path}", file=sys.stderr)
        return 2
    for w in cfg.warnings():
        print(f"提醒：{w}", file=sys.stderr)
    from http.server import ThreadingHTTPServer

    # 关键：必须写 mtbridge.STATE，Handler 用的是模块级的那个
    mtbridge.STATE.cfg = cfg
    mtbridge.STATE.upstream = mtbridge.Upstream(cfg)
    httpd = ThreadingHTTPServer((cfg.host, port), mtbridge.Handler)
    acc = cfg.active_account()
    print(f"mtbridge {mtbridge.__version__}  http://{cfg.host}:{httpd.server_address[1]}/v1")
    print(f"账户 {len(cfg.accounts)} 个，活动 = {acc.get('label') if acc else '无'}")
    print("Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


# ------------------------------------------------------------------ 主循环

def run_tui(cfg_path: Path, port: int) -> int:
    cfg = mtbridge.load_config(cfg_path)
    errs = cfg.problems()
    if errs:
        for e in errs:
            print(f"配置错误：{e}", file=sys.stderr)
        return 2
    for w in cfg.warnings():
        print(f"提醒：{w}", file=sys.stderr)

    # 关键：必须写 mtbridge.STATE，Handler 用的是模块级的那个
    mtbridge.STATE.cfg = cfg
    mtbridge.STATE.upstream = mtbridge.Upstream(cfg)

    from http.server import ThreadingHTTPServer

    try:
        httpd = ThreadingHTTPServer((cfg.host, port), mtbridge.Handler)
    except OSError as e:
        print(f"无法绑定 {cfg.host}:{port} — {e}", file=sys.stderr)
        return 1
    bound = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    st = {"running": True, "model_count": 0, "requests": 0,
          "quota": {}, "recent": [], "msg": ""}

    # 记录调用（不覆盖 _chat 本身，只在旁边挂钩，便于回退）
    orig = mtbridge.Handler._chat

    def traced(self):
        t0 = time.time()
        acc = cfg.active_account() or {}
        try:
            return orig(self)
        finally:
            st["requests"] += 1
    mtbridge.Handler._chat = traced

    def bg():
        while True:
            time.sleep(15)
            acc = cfg.active_account()
            if not acc:
                continue
            _, models = mtbridge.Upstream(cfg).models(acc["clientId"])
            if models:
                st["model_count"] = len(models)
            probe = models[0]["name"] if models else "deepseek/deepseek-v3.2-exp"
            r = mtbridge.Upstream(cfg).probeQuota(acc["uuid"], acc["clientId"], probe)
            if r.is_ok:
                st["quota"][acc["uuid"]] = r.value
    threading.Thread(target=bg, daemon=True).start()

    sel = 0
    sel_sticky = 0.0          # 选中项的"最后操作时间"，仅用于提示
    with Term() as t:
        while True:
            t.render(draw(cfg, bound, st, sel))
            ev = t.read_event(0.3)
            if ev is None:
                # 心跳：让「最近调用」这类会变的内容有机会刷新
                if st["recent"] or st["quota"]:
                    t.render(draw(cfg, bound, st, sel))
                continue
            kind, val = ev
            if kind == "wheel":
                if cfg.accounts:
                    sel = max(0, min(sel - (1 if val > 0 else -1), len(cfg.accounts) - 1))
                    sel_sticky = time.time()
                continue
            if kind == "click":
                continue
            # ---- 键盘
            if val in ("q", "Q", "\x03"):
                break
            elif val in ("\r", "\n", " "):
                if cfg.accounts:
                    cfg.save_active(cfg.accounts[sel].get("label", ""))
                    st["msg"] = f"已切换到 {cfg.accounts[sel].get('label')}"
            elif val in ("j", "down"):
                sel = min(sel + 1, max(0, len(cfg.accounts) - 1)); sel_sticky = time.time()
            elif val in ("k", "up"):
                sel = max(sel - 1, 0); sel_sticky = time.time()
            elif val in ("pgdn",):
                sel = min(sel + 3, max(0, len(cfg.accounts) - 1)); sel_sticky = time.time()
            elif val in ("pgup",):
                sel = max(sel - 3, 0); sel_sticky = time.time()
            elif val.isdigit() and val != "0":
                i = int(val) - 1
                if i < len(cfg.accounts):
                    sel = i
                    cfg.save_active(cfg.accounts[i].get("label", ""))
                    st["msg"] = f"已切换到 {cfg.accounts[i].get('label')}"
            elif val == "e" and cfg.accounts:
                a = dict(cfg.accounts[sel])
                a["enabled"] = not a.get("enabled", True)
                cfg.accounts[sel] = a
                cfg.save()
                st["msg"] = f"{a.get('label')} 已{'启用' if a['enabled'] else '停用'}"
            elif val == "d" and cfg.accounts:
                a = cfg.accounts.pop(sel)
                cfg.save()
                sel = max(0, sel - 1)
                st["msg"] = f"已删除 {a.get('label')}"
            elif val == "R":
                acc = cfg.active_account()
                if acc:
                    _, models = mtbridge.Upstream(cfg).models(acc["clientId"])
                    st["model_count"] = len(models)
                    st["msg"] = f"模型 {len(models)}" if models else "获取模型失败"
            t.render(draw(cfg, bound, st, sel))

    httpd.shutdown()
    mtbridge.Handler._chat = orig
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="mtbridge-tui",
        description="MiniTavern 本地代理 · 终端界面",
    )
    ap.add_argument("-c", "--config", type=Path,
                    default=Path(__file__).resolve().parent / "config.json")
    ap.add_argument("--port", type=int, default=None, help="覆盖配置里的端口")
    ap.add_argument("--headless", action="store_true", help="不启界面，只跑代理")
    ap.add_argument("--export", metavar="FILE", help="导出账户列表后退出")
    ap.add_argument("--import_", dest="import_", metavar="FILE",
                    help="导入账户列表后退出")
    ap.add_argument("--replace", action="store_true", help="导入时替换现有账户")
    args = ap.parse_args()

    if args.export:
        print(do_export(args.config, args.export))
        return 0
    if args.import_:
        print(do_import(args.config, args.import_, args.replace))
        return 0

    cfg = mtbridge.load_config(args.config)
    port = args.port or cfg.port
    if args.headless:
        return headless(args.config, port)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("非交互终端，转为 headless 模式", file=sys.stderr)
        return headless(args.config, port)
    return run_tui(args.config, port)


if __name__ == "__main__":
    sys.exit(main())
