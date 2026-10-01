#!/usr/bin/env python3
"""
mtbridge-tui — mtbridge 的终端界面。

设计参照 GLM-Free-API 的 token-collector（bubbletea + lipgloss）那套做法，
翻译成 Python 标准库实现，零第三方依赖：

1. 状态集中在一个 dict，网络 I/O 全部丢进后台线程；
   **事件循环里绝不做阻塞调用** —— 之前 R 键刷新和 t 键开户都是直接
   在循环里发网络请求，界面整个冻住，实测要等十几秒才有反应。
2. 渲染由 ticker 驱动（约 5 fps），事件循环只负责收键。
3. 整帧渲染，但**内容没变就一个字节都不写**，避免闪烁。
   （之前做的是逐行差分，逻辑一复杂就会和光标位置失步，表现为残影）
4. 底部固定日志面板，可滚动。

运行：
    python mtbridge_tui.py
    python mtbridge_tui.py --headless
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import shutil
import sys
import threading
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mtbridge  # noqa: E402

IS_WIN = sys.platform == "win32"
TICK = 0.2                      # 渲染间隔
LOG_MAX = 400

# ---------------------------------------------------------------- 颜色

A = "\x1b["


def c(text, *styles):
    return f"{A}{';'.join(styles)}m{text}{A}0m" if styles else text


BOLD = "1"
DIM = "2"
TEAL = "38;5;79"
GREEN = "38;5;114"
YELLOW = "38;5;221m"[0:-1]
RED = "38;5;203"
GREY = "38;5;245"
DIMGREY = "38;5;240"
WHITE = "38;5;255"
BGV = "48;5;236"
CYAN = "38;5;99"


def vis(s: str) -> str:
    """去掉 ANSI 的可见宽度。"""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def cut(s: str, w: int) -> str:
    """按可见宽度截断（不处理宽字符，够用）。"""
    if len(vis(s)) <= w:
        return s
    out, n = [], 0
    i = 0
    while i < len(s):
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j < 0:
                break
            out.append(s[i:j + 1])
            i = j + 1
            continue
        if n >= w:
            break
        out.append(s[i])
        n += 1
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- 状态


class S:
    """全局状态。所有字段由后台线程写、渲染线程读。"""

    def __init__(self, cfg, port):
        self.lock = threading.Lock()
        self.cfg = cfg
        self.port = port
        self.running = True
        self.sel = 0
        self.model_count = 0
        self.requests = 0
        self.quota: dict[str, dict] = {}
        self.recent: deque = deque(maxlen=6)
        self.msg = ""
        self.logs: deque = deque(maxlen=LOG_MAX)
        self.log_off = 0
        self.busy = ""            # 正在做的事，显示在状态行
        self.diag = False         # 诊断：把收到的按键写进日志
        self.dirty = True

    def log(self, msg, kind="info"):
        ts = time.strftime("%H:%M:%S")
        with self.lock:
            self.logs.append(f"{ts}  {msg}")
        self.dirty = True

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)
        self.dirty = True

    def accounts(self):
        return self.cfg.accounts

    def active(self):
        return self.cfg.active_account()


# ---------------------------------------------------------------- 终端


class Term:
    def __init__(self):
        self.out = sys.stdout
        self.tty = sys.stdin.isatty() and sys.stdout.isatty()
        self._prev_frame = None
        self._k32 = self._h = self._mode = None
        self._oh = self._om = None
        self._old = None

    def __enter__(self):
        if IS_WIN:
            self._win()
        else:
            import termios
            import tty

            self._old = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        self.out.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")
        self.out.flush()
        self._prev_frame = None
        return self

    def __exit__(self, *e):
        self.out.write("\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[0m\x1b[?1049l")
        self.out.flush()
        if IS_WIN:
            if self._k32 and self._h is not None:
                self._k32.SetConsoleMode(self._h, self._mode)
            if self._oh is not None:
                ctypes.windll.kernel32.SetConsoleMode(self._oh, self._om)
        else:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old)

    def _win(self):
        k32 = ctypes.windll.kernel32
        # stdout: 打开 VT 输出，否则 \x1b[K 不清行，会留残影
        oh = k32.GetStdHandle(-11)
        om = ctypes.c_uint32()
        if k32.GetConsoleMode(oh, ctypes.byref(om)):
            k32.SetConsoleMode(oh, om.value | 0x0004)
            self._oh, self._om = oh, om.value
        # stdin: 打开 VT 输入，方向键/鼠标都转成 ANSI 序列
        h = k32.GetStdHandle(-10)
        m = ctypes.c_uint32()
        if not k32.GetConsoleMode(h, ctypes.byref(m)):
            self.tty = False
            return
        k32.SetConsoleMode(h, m.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_INPUT
        self._k32, self._h, self._mode = k32, h, m.value

    def size(self):
        try:
            s = shutil.get_terminal_size()
            return max(60, s.columns), max(18, s.lines)
        except Exception:
            return 100, 30

    def draw(self, lines: list[str]) -> bool:
        w, h = self.size()
        frame = [cut(x, w - 1) for x in lines[: h - 1]]
        if frame == self._prev_frame:
            return False
        out = ["\x1b[H"]
        for ln in frame:
            out.append("\x1b[K" + ln + "\n")
        # 多余的行清掉
        for _ in range(max(0, len(self._prev_frame or []) - len(frame))):
            out.append("\x1b[K\n")
        self.out.write("".join(out))
        self.out.flush()
        self._prev_frame = frame
        return True

    def key(self, timeout: float) -> str | None:
        """阻塞等一个按键，返回键值；ESC 开头的返回 ''。超时返回 None。"""
        if not self.tty:
            time.sleep(timeout)
            return None
        if IS_WIN:
            import msvcrt

            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch != "\x1b":
                        return ch
                    return self._seq()       # 方向键/鼠标，事件循环另行忽略
                time.sleep(timeout)
                if msvcrt.kbhit():
                    continue
                return None
        import select

        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            return self._seq()
        return ch

    def _seq(self):
        """读完一个 ESC 序列，翻译成事件；不是鼠标/方向键则返回空串。"""
        seq = ""
        for _ in range(24):
            ch = sys.stdin.read(1)
            if not ch:
                break
            seq += ch
            if ch.isalpha() or ch == "~":
                break
        if seq.startswith("[<"):
            p = seq[2:].split(";")
            if len(p) >= 3:
                if p[0] in ("A", "B"):
                    return "\x01up" if p[0] == "A" else "\x01down"
                if p[0] in ("M", "m"):
                    return "\x02click"
            return ""
        return {
            "[A": "up", "[B": "down", "[C": "right", "[D": "left",
            "OA": "up", "OB": "down", "OC": "right", "OD": "left",
            "[5": "pgup", "[6": "pgdn",
        }.get(seq, "")


# ---------------------------------------------------------------- 渲染


def render(st: S) -> list[str]:
    w = max(60, shutil.get_terminal_size().columns)
    h = max(18, shutil.get_terminal_size().lines)
    accs = st.accounts()
    sel = max(0, min(st.sel, len(accs) - 1)) if accs else 0
    act = st.active()
    out = []

    # ---- 顶栏
    out.append("  " + c("MiniTavern Bridge", WHITE, BOLD)
               + c("  " + c(f"v{mtbridge.__version__}", GREY), GREY)
               + ("  " + c("● 运行中", GREEN, BOLD) if st.running
                  else "  " + c("○ 已停止", RED, BOLD)))
    out.append("  " + c(f"http://127.0.0.1:{st.port}/v1", TEAL))
    out.append("  " + c(f"账户 {len(accs)}   模型 {st.model_count or '—'}   "
                          f"请求 {st.requests}", GREY))
    if act:
        line = "  活动  " + c(act.get("label", "?"), TEAL, BOLD)
        q = st.quota.get(act["uuid"])
        if q:
            line += c(f"   配额 {q['used']}/{q['total']}",
                      YELLOW if q["used"] < q["total"] else RED)
        out.append(line)
    else:
        out.append("  活动  " + c("无 —— 还没有账户", RED, BOLD))
    if st.busy:
        out.append("  " + c("… " + st.busy, YELLOW))
    elif st.msg:
        out.append("  " + c(st.msg, YELLOW))
    out.append("")

    # ---- 最近调用
    out.append("  " + c("最近调用", WHITE, BOLD))
    if not st.recent:
        out.append("    " + c("暂无", DIMGREY))
    for r in reversed(list(st.recent)):
        col = RED if r.get("err") else (TEAL if (r.get("status") or 0) < 300 else YELLOW)
        out.append("    " + c(r.get("t", ""), GREY) + "  "
                   + c(f"{r.get('model','?').rsplit('/',1)[-1]:<24}", col)
                   + c(f"{r.get('ms',0):>6}ms ", GREY)
                   + c(f"HTTP {r.get('status','-')}", col)
                   + (c("  " + r["err"], RED) if r.get("err") else ""))
    out.append("")

    # ---- 账户表（分掉日志区之后剩下的高度）
    log_h = max(3, min(12, h // 3))
    acct_h = max(1, h - len(out) - log_h - 3)
    out.append("  " + c("账户", WHITE, BOLD)
               + c("   ↑↓ / j k / 滚轮 选择 · Enter 切换 · e 停用 · d 删除 · R 刷新 · q 退出",
                   GREY))
    if not accs:
        out.append("    " + c("（空）编辑 config.json 或用 --import_ 导入", DIMGREY))
    for i, a in enumerate(accs[:acct_h]):
        on = a["uuid"] == (act or {}).get("uuid")
        mark = c("▶", TEAL, BOLD) if on else " "
        body = (f"{mark} {a.get('label','?'):<14} {a.get('uuid','')[:16]}…")
        tail = ""
        if not a.get("enabled", True):
            tail += c("  [停用]", RED)
        q = st.quota.get(a["uuid"])
        if q:
            tail += c(f"  {q['used']}/{q['total']}", GREY)
        line = "  " + body + tail
        out.append(c(line.ljust(w - 1), BGV, WHITE, BOLD) if i == sel
                   else (c(line, TEAL) if on else c(line, GREY)))

    # ---- 日志面板
    out.append("  " + c("─" * (w - 4), DIMGREY))
    out.append("  " + c("日志", WHITE, BOLD)
               + c("   g/G 跳到最新/最早 · ↑↓ 滚动", GREY))
    with st.lock:
        logs = list(st.logs)
    view = log_h
    end = len(logs) - st.log_off
    start = max(0, end - view)
    seg = logs[start:end]
    while len(seg) < view:
        seg.insert(0, "")
    for ln in seg:
        out.append(c(cut("  " + ln, w - 2), DIMGREY) if ln else "")
    return out


# ---------------------------------------------------------------- 后台


def workers(st: S, httpd):
    """所有网络 I/O 都在这里，主循环不碰。"""
    cfg = st.cfg

    def refresh_quota(acc):
        up = mtbridge.Upstream(cfg)
        _, models = up.models(acc["clientId"])
        if models:
            st.set(model_count=len(models))
        q = up.probe_quota(acc, models[0]["name"] if models
                           else "deepseek/deepseek-v3.2-exp")
        if q:
            with st.lock:
                st.quota[acc["uuid"]] = q
        return len(models)

    def periodic():
        while st.running:
            time.sleep(12)
            acc = st.active()
            if not acc:
                continue
            try:
                refresh_quota(acc)
                st.dirty = True
            except Exception as e:  # noqa: BLE001
                st.log(f"刷新配额失败：{e}", "err")

    threading.Thread(target=periodic, daemon=True).start()

    # 记录调用
    orig = mtbridge.Handler._chat

    def traced(self):
        t0 = time.time()
        acc = cfg.active_account() or {}
        model = "?"
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            model = json.loads(raw).get("model", "?") if raw else "?"
        except Exception:  # noqa: BLE001
            pass
        status, err = None, None
        try:
            return orig(self)
        except Exception as e:  # noqa: BLE001
            status, err = getattr(e, "code", 502), str(e)[:60]
            raise
        finally:
            st.set(requests=st.requests + 1)
            with st.lock:
                st.recent.append({
                    "t": time.strftime("%H:%M:%S"), "model": model,
                    "ms": int((time.time() - t0) * 1000),
                    "status": status, "err": err})
            st.dirty = True
    mtbridge.Handler._chat = traced


def bg_job(st: S, what: str, fn):
    """把一次可能较慢的操作丢到线程池，主循环立刻返回。"""
    def run():
        st.set(busy=what)
        st.log(f"开始：{what}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            st.set(msg=f"{what} 失败：{e}")
            st.log(f"{what} 失败：{e}", "err")
        else:
            st.log(f"完成：{what}")
        st.set(busy="")
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- 主循环


def handle_key(st: S, cfg, k: str, ctx: dict) -> bool:
    """处理一个按键。返回 True 表示退出主循环。

    抽成独立函数是为了能脱离终端直接测试每个键的分支 ——
    之前手势逻辑埋在 while 循环里，出问题没法定位。
    """
    accs = cfg.accounts
    n = len(accs)
    sel = max(0, min(st.sel, n - 1)) if n else 0

    if k in ("q", "Q", "\x03"):
        return True

    # ---- 选择：方向键 / 滚轮 / vim 键 / 数字
    if k in ("\x01up", "up", "k"):
        if n:
            st.set(sel=max(0, sel - 1))
        return False
    if k in ("\x01down", "down", "j"):
        if n:
            st.set(sel=min(n - 1, sel + 1))
        return False
    if k == "pgup":
        if n:
            st.set(sel=max(0, sel - 3))
        return False
    if k == "pgdn":
        if n:
            st.set(sel=min(n - 1, sel + 3))
        return False
    if k.isdigit() and k != "0":
        i = int(k) - 1
        if 0 <= i < n:
            st.set(sel=i)
            cfg.save_active(accs[i].get("label", ""))
            st.set(msg=f"已切换到 {accs[i].get('label')}")
        return False

    if k in ("\r", "\n", " "):
        if n:
            cfg.save_active(accs[sel].get("label", ""))
            st.set(msg=f"已切换到 {accs[sel].get('label')}")
            st.log(f"切换账户 -> {accs[sel].get('label')}")
        return False
    if k == "e" and n:
        a = dict(accs[sel])
        a["enabled"] = not a.get("enabled", True)
        cfg.accounts[sel] = a
        cfg.save()
        st.set(msg=f"{a.get('label')} 已{'启用' if a['enabled'] else '停用'}")
        st.log(f"{a.get('label')} -> {'启用' if a['enabled'] else '停用'}")
        return False
    if k == "d" and n:
        a = cfg.accounts.pop(sel)
        cfg.save()
        st.set(sel=max(0, sel - 1), msg=f"已删除 {a.get('label')}")
        st.log(f"删除账户 {a.get('label')}")
        return False
    if k == "R":
        acc = cfg.active_account()
        if acc:
            bg_job(st, "刷新", lambda: _refresh(st, acc))
        else:
            st.set(msg="没有活动账户")
        return False
    if k == "g":
        st.set(log_off=0)
        return False
    if k == "G":
        with st.lock:
            st.log_off = max(0, len(st.logs) - 3)
        return False
    if k == "t":
        now = time.time()
        ctx["t_taps"] = (ctx["t_taps"] + 1
                         if now - ctx["last_tap"] < 3.0 else 1)
        ctx["last_tap"] = now
        if ctx["t_taps"] >= 5:
            ctx["t_taps"] = 0
            bg_job(st, "开户", lambda: _provision(st))
        elif ctx["t_taps"] >= 3:
            st.set(msg=f"再按 {5 - ctx['t_taps']} 次 t 解锁调试入口")
        return False

    ctx["t_taps"] = 0
    return False



def run_tui(cfg_path: Path, port: int) -> int:
    cfg = mtbridge.load_config(cfg_path)
    errs = cfg.problems()
    if errs:
        for e in errs:
            print(f"配置错误：{e}", file=sys.stderr)
        return 2
    for w in cfg.warnings():
        print(f"提醒：{w}", file=sys.stderr)

    mtbridge.STATE.cfg = cfg                       # 必须是模块级的那个
    mtbridge.STATE.upstream = mtbridge.Upstream(cfg)

    from http.server import ThreadingHTTPServer

    try:
        httpd = ThreadingHTTPServer((cfg.host, port), mtbridge.Handler)
    except OSError as e:
        print(f"无法绑定 {cfg.host}:{port} — {e}", file=sys.stderr)
        return 1
    bound = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    st = S(cfg, bound)
    st.log(f"代理已启动 127.0.0.1:{bound}  pid={os.getpid()}")
    st.log(f"账户 {len(cfg.accounts)} 个")
    st.diag = os.environ.get("MTB_DIAG") == "1"   # 诊断模式：把收到的按键写进日志
    if st.diag:
        print("诊断模式已开启，按键会记录到日志面板", file=sys.stderr)
    workers(st, httpd)

    ctx = {"t_taps": 0, "last_tap": 0.0}
    with Term() as t:
        while st.running:
            t.draw(render(st))
            k = t.key(TICK)
            if k is None:
                continue
            accs = cfg.accounts
            n = len(accs)
            sel = max(0, min(st.sel, n - 1)) if n else 0

            if st.diag:
                st.log(f"收到按键 {k!r}  选中={sel}", "diag")
            if handle_key(st, cfg, k, ctx):
                break

    st.running = False
    httpd.shutdown()
    return 0


def _refresh(st: S, acc):
    up = mtbridge.Upstream(st.cfg)
    _, models = up.models(acc["clientId"])
    st.set(model_count=len(models), msg=f"模型 {len(models)}" if models else "获取模型失败")
    q = up.probe_quota(acc, models[0]["name"] if models
                       else "deepseek/deepseek-v3.2-exp")
    if q:
        with st.lock:
            st.quota[acc["uuid"]] = q
        st.set(msg=f"配额 {q['used']}/{q['total']}")


def _provision(st: S):
    acc = mtbridge.add_test_account(st.cfg)
    st.set(sel=max(0, len(st.cfg.accounts) - 1),
           msg=f"已创建 {acc.get('label')}")
    st.log(f"开户成功 {acc.get('label')}")


def headless(cfg_path: Path, port: int) -> int:
    cfg = mtbridge.load_config(cfg_path)
    errs = cfg.problems()
    if errs:
        for e in errs:
            print(f"配置错误：{e}", file=sys.stderr)
        return 2
    for w in cfg.warnings():
        print(f"提醒：{w}", file=sys.stderr)
    from http.server import ThreadingHTTPServer

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


# ---------------------------------------------------------------- 入口


def main() -> int:
    ap = argparse.ArgumentParser(prog="mtbridge-tui",
                                 description="MiniTavern 本地代理 · 终端界面")
    ap.add_argument("-c", "--config", type=Path,
                    default=Path(__file__).resolve().parent / "config.json")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--headless", action="store_true", help="不启界面，只跑代理")
    ap.add_argument("--export", metavar="FILE")
    ap.add_argument("--import_", dest="import_", metavar="FILE")
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args()

    if args.export:
        cfg = mtbridge.load_config(args.config)
        Path(args.export).write_text(cfg.export_json(), encoding="utf-8")
        print(f"已导出 {len(cfg.accounts)} 个账户 -> {args.export}")
        return 0
    if args.import_:
        cfg = mtbridge.load_config(args.config)
        a, sk = cfg.import_json(Path(args.import_).read_text(encoding="utf-8"),
                                replace=args.replace)
        print(f"导入完成：新增 {a}，跳过重复 {sk}")
        return 0

    cfg = mtbridge.load_config(args.config)
    port = args.port or cfg.port
    if args.headless:
        return headless(args.config, port)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("非交互终端，转 headless 模式", file=sys.stderr)
        return headless(args.config, port)
    return run_tui(args.config, port)


if __name__ == "__main__":
    sys.exit(main())
