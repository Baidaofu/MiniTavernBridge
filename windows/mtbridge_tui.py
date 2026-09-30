#!/usr/bin/env python3
"""
mtbridge-tui — mtbridge 的终端界面。

零第三方依赖，用标准库 curses 实现（Windows 走 colorama 之外的
windows-curses 兼容层，这里直接用 ANSI + 原始模式，避免额外依赖）。

功能
----
- 实时面板：服务状态、活动账户、模型数、配额、最近调用
- 账户管理：切换 / 启用停用 / 重命名 / 删除
- 导入导出：文件选择由外部路径参数完成，避免在终端里做文件浏览器
- 键位在底部常驻显示

运行
----
    python mtbridge_tui.py                 # 前台运行代理 + TUI
    python mtbridge_tui.py --headless      # 只跑代理，不开界面
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mtbridge  # noqa: E402


# ------------------------------------------------------------------ 终端原语

class Term:
    """极简 ANSI 终端封装，不依赖 curses。"""

    def __init__(self) -> None:
        self.out = sys.stdout
        self.raw = False
        self._old = None

    def __enter__(self):
        if sys.platform == "win32":
            import msvcrt  # noqa: F401
        else:
            import termios
            import tty

            self._old = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        self.raw = True
        sys.stdout.write("\x1b[?1049h")  # 备用屏
        return self

    def __exit__(self, *exc):
        if sys.platform == "win32":
            pass
        else:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old)
        sys.stdout.write("\x1b[?1049l")  # 恢复
        self.raw = False

    def clear(self):
        self.out.write("\x1b[2J\x1b[H")

    def move(self, r, c):
        self.out.write(f"\x1b[{r};1H\x1b[{c}C")

    def write(self, s, fg=None, bg=None, bold=False, dim=False):
        codes = []
        if fg:
            codes.append(f"38;5;{fg}")
        if bg:
            codes.append(f"48;5;{bg}")
        if bold:
            codes.append("1")
        if dim:
            codes.append("2")
        if codes:
            self.out.write("\x1b[" + ";".join(codes) + "m")
        self.out.write(s)
        if codes:
            self.out.write("\x1b[0m")

    def size(self):
        try:
            sz = shutil.get_terminal_size()
            return max(60, sz.columns), max(20, sz.lines)
        except Exception:
            return 100, 30

    def flush(self):
        self.out.flush()

    def read_key(self):
        """返回单个按键；无输入返回 ''。"""
        if sys.platform == "win32":
            import msvcrt

            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                # 方向键前缀
                if ch in ("\x00", "\xe0"):
                    code = msvcrt.getwch()
                    return {"H": "up", "P": "down", "K": "left",
                            "M": "right", "G": "home", "O": "end"}.get(code, "")
                return ch
            return ""
        import select

        r, _, _ = select.select([sys.stdin], [], [], 0)
        if not r:
            return ""
        return sys.stdin.read(1)


# ------------------------------------------------------------------ 面板渲染

CLR = {
    "reset": "\x1b[0m", "dim": "\x1b[2m", "bold": "\x1b[1m",
    "teal": "\x1b[38;5;79m", "green": "\x1b[38;5;114m",
    "yellow": "\x1b[38;5;221m", "red": "\x1b[38;5;203m",
    "grey": "\x1b[38;5;245m", "white": "\x1b[38;5;255m",
    "inv": "\x1b[48;5;236m",
}


def draw(t: Term, cfg: mtbridge.Config, proxy_port: int, state: dict, sel: int):
    w, h = t.size()
    t.clear()
    accounts = cfg.accounts
    if accounts:
        sel = max(0, min(sel, len(accounts) - 1))
    active = cfg.active_account()

    # ---- 顶栏
    t.write("  MiniTavern Bridge TUI", fg=79, bold=True)
    t.write("   v" + mtbridge.__version__, fg=245, dim=True)
    running = state.get("running")
    t.write(
        f"   {'● 运行中' if running else '○ 已停止'}",
        fg=114 if running else 203, bold=True,
    )
    t.write(f"   http://127.0.0.1:{proxy_port}/v1", fg=245, dim=True)

    # ---- 状态块
    t.write("\n\n  ")
    t.write("活动账户  ", fg=245)
    t.write((active.get("label") if active else "无") or "无",
            fg=79, bold=True)
    if active:
        q = state.get("quota", {}).get(active["uuid"])
        t.write("   ")
        if q:
            t.write(f"配额 {q['used']}/{q['total']}",
                    fg=221 if q["used"] < q["total"] else 203)
        else:
            t.write("配额 未探测", fg=245, dim=True)
    t.write("   ")
    t.write(f"账户 {len(accounts)}", fg=245)
    t.write("   ")
    n = state.get("model_count", 0)
    t.write(f"模型 {n or '—'}", fg=245 if n else 203)
    t.write("   ")
    t.write(f"请求 {state.get('requests', 0)}", fg=245)

    # ---- 最近调用
    t.write("\n\n")
    t.write("  最近调用", fg=255, bold=True)
    calls = state.get("recent", [])
    if not calls:
        t.write("  暂无", fg=245, dim=True)
    for c in reversed(calls[-4:]):
        col = 203 if c.get("error") else (79 if c.get("status", 0) < 300 else 221)
        t.write(f"\n    {c.get('time','')}  ", fg=245, dim=True)
        t.write(f"{mtbridge_log_short(c.get('model','')):<28}", fg=col)
        t.write(f"{c.get('latency_ms', 0):>6} ms  ", fg=245, dim=True)
        t.write(f"HTTP {c.get('status', '-')}", fg=col)

    # ---- 账户表
    rows = h - 13
    if rows < 3:
        rows = 3
    t.write("\n\n")
    t.write("  账户", fg=255, bold=True)
    t.write(f"   ↑↓ 选择 · Enter 切换 · e 停用/启用 · d 删除 · r 重命名",
            fg=245, dim=True)
    t.write("\n")

    if not accounts:
        t.write("    （无）", fg=245, dim=True)
    for i, a in enumerate(accounts[:rows]):
        is_act = a.get("uuid") == (active or {}).get("uuid")
        line = f"  {'▶' if is_act else ' '} {a.get('label','?'):<18} {a.get('uuid','')[:16]}…"
        if not a.get("enabled", True):
            line += "  [已停用]"
        q = state.get("quota", {}).get(a["uuid"])
        if q:
            line += f"   {q['used']}/{q['total']}"
        t.write("\n")
        if i == sel:
            t.write(line.ljust(w - 1)[: w - 1], bg=236, fg=255, bold=True)
        else:
            t.write(line[: w - 1],
                    fg=79 if is_act else 245,
                    dim=not a.get("enabled", True))

    # ---- 状态栏
    t.move(h, 0)
    msg = state.get("msg", "")
    t.write(f"  {msg}"[: w - 1].ljust(w - 1), bg=236, fg=255)


def mtbridge_log_short(m: str) -> str:
    return m.rsplit("/", 1)[-1][:28]


# ------------------------------------------------------------------ 账户操作

def do_export(cfg_path: Path, out: str) -> str:
    cfg = mtbridge.load_config(cfg_path)
    dst = Path(out)
    dst.write_text(cfg.export_json(), encoding="utf-8")
    return f"已导出 {len(cfg.accounts)} 个账户 → {dst}"


def do_import(cfg_path: Path, src: str, replace: bool) -> str:
    cfg = mtbridge.load_config(cfg_path)
    text = Path(src).read_text(encoding="utf-8")
    added, skipped = cfg.import_json(text, replace=replace)
    return f"导入完成：新增 {added}，跳过重复 {skipped}"


# ------------------------------------------------------------------ 主循环

def run_tui(cfg_path: Path, port: int) -> int:
    cfg = mtbridge.load_config(cfg_path)
    errs = cfg.problems()
    if errs:
        for e in errs:
            print(f"配置错误：{e}", file=sys.stderr)
        return 2

    STATE = mtbridge.State()
    STATE.cfg = cfg
    STATE.upstream = mtbridge.Upstream(cfg)

    try:
        from http.server import ThreadingHTTPServer

        httpd = ThreadingHTTPServer((cfg.host, port), mtbridge.Handler)
    except OSError as e:
        print(f"无法绑定 {cfg.host}:{port} — {e}", file=sys.stderr)
        return 1

    bound = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    state = {"running": True, "model_count": 0, "requests": 0,
             "quota": {}, "recent": [], "msg": ""}

    def background():
        while True:
            time.sleep(20)
            acc = cfg.active_account()
            if not acc:
                continue
            err, models = STATE.upstream.models(acc["clientId"])
            if models:
                state["model_count"] = len(models)
            probe = models[0]["name"] if models else "deepseek/deepseek-v3.2-exp"
            r = mtbridge.Upstream(cfg).probeQuota(
                acc["uuid"], acc["clientId"], probe)
            if r.is_ok:
                state["quota"][acc["uuid"]] = r.value
    threading.Thread(target=background, daemon=True).start()

    # 记录调用
    orig_call = mtbridge.Handler._chat

    def patched(self):
        try:
            orig_call(self)
        finally:
            state["requests"] += 1
    mtbridge.Handler._chat = patched

    sel = 0
    last = 0.0
    with Term() as t:
        while True:
            now = time.time()
            if now - last > 0.15:
                draw(t, cfg, bound, state, sel)
                last = now
            k = t.read_key()
            if not k:
                continue
            if k in ("q", "Q", "\x03"):
                state["msg"] = "退出"
                break
            elif k in ("\r", "\n", " "):
                if cfg.accounts:
                    cfg.save_active(cfg.accounts[sel].get("label", ""))
                    state["msg"] = f"已切换到 {cfg.accounts[sel].get('label')}"
            elif k in ("j", "down"):
                sel = min(sel + 1, max(0, len(cfg.accounts) - 1))
            elif k in ("k", "up"):
                sel = max(sel - 1, 0)
            elif k == "e" and cfg.accounts:
                a = dict(cfg.accounts[sel])
                a["enabled"] = not a.get("enabled", True)
                cfg.accounts[sel] = a
                cfg.save(cfg)
                state["msg"] = f"{a['label']} 已{'启用' if a['enabled'] else '停用'}"
            elif k == "d" and cfg.accounts:
                a = cfg.accounts.pop(sel)
                cfg.save(cfg)
                sel = max(0, sel - 1)
                state["msg"] = f"已删除 {a.get('label')}"
            elif k == "r" and cfg.accounts:
                state["msg"] = "重命名请编辑 config.json 的 label 字段"
            elif k == "R":
                acc = cfg.active_account()
                if acc:
                    err, models = STATE.upstream.models(acc["clientId"])
                    state["model_count"] = len(models)
                    state["msg"] = f"模型 {len(models)}" if models else f"失败：{err}"

    httpd.shutdown()
    return 0


def run_headless(cfg_path: Path, port: int) -> int:
    return mtbridge.main_impl(cfg_path, port) if hasattr(mtbridge, "main_impl") else _fallback(cfg_path, port)


def _fallback(cfg_path: Path, port: int) -> int:
    cfg = mtbridge.load_config(cfg_path)
    errs = cfg.problems()
    if errs:
        for e in errs:
            print(f"配置错误：{e}", file=sys.stderr)
        return 2
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
    ap.add_argument("--import_", dest="import_", metavar="FILE", help="导入账户列表后退出")
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
        return _fallback(args.config, port)
    if sys.platform == "win32" and not sys.stdout.isatty():
        print("检测到非交互终端，自动转为 headless 模式", file=sys.stderr)
        return _fallback(args.config, port)
    return run_tui(args.config, port)


if __name__ == "__main__":
    sys.exit(main())
