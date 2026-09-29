#!/usr/bin/env python3
"""
mtbridge — MiniTavern 本地代理（Windows / 命令行版）

把 MiniTavern 会员代理包装成一个 OpenAI 兼容端点。

为什么需要它
------------
MiniTavern 后端有三个特性让通用客户端接不上：

  1. GET /api/ai-proxy/models 不存在 —— 客户端拉不到模型列表
  2. 必须带 X-Client-Id 请求头
  3. uuid 必须在 JSON 请求体里

第 3 点是死结：pi / Kelivo 这类客户端只能设置 header，不能往 body 注入字段。
本程序就是中间那层转换。

所有参数都从配置文件读取，代码里没有任何写死的地址、端口或凭据。

用法
----
    python mtbridge.py                 # 用 ./config.json 启动
    python mtbridge.py -c other.json   # 指定配置文件
    python mtbridge.py --check         # 只检查配置和网络，不启动服务
    python mtbridge.py --test          # 启动并自检
    python mtbridge.py --active "手机B" # 临时切换活动账户
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.json"


# --------------------------------------------------------------------- 配置

class Config:
    """运行时配置。所有字段都来自 JSON，缺省值仅用于报错提示。"""

    def __init__(self, path: Path, data: dict):
        self.path = path
        self._raw = data
        listen = data.get("listen", {})
        upstream = data.get("upstream", {})
        self.host: str = listen.get("host", "127.0.0.1")
        self.port: int = int(listen.get("port", 0))
        self.base_url: str = upstream.get("base", "").rstrip("/")
        self.accounts: list[dict] = data.get("accounts", [])
        self.active_label: str = data.get("active", "")
        self.model_cache_ttl: int = int(data.get("model_cache_ttl_seconds", 300))
        self.request_timeout: int = int(data.get("request_timeout_seconds", 300))
        self.verify_tls: bool = bool(data.get("verify_tls", True))
        self.verbose: bool = bool(data.get("verbose", False))

    # -- 校验 ---------------------------------------------------------------

    def problems(self) -> list[str]:
        errs: list[str] = []
        if not self.base_url:
            errs.append('缺少 upstream.base')
        elif not self.base_url.startswith(("http://", "https://")):
            errs.append(f"upstream.base 必须以 http:// 或 https:// 开头：{self.base_url}")
        if self.port <= 0 or self.port > 65535:
            errs.append(f"listen.port 非法：{self.port}")
        if not self.accounts:
            errs.append("accounts 为空")
        for i, a in enumerate(self.accounts):
            if not a.get("uuid"):
                errs.append(f"accounts[{i}] ({a.get('label', '?')}) 缺少 uuid")
            if not a.get("clientId"):
                errs.append(f"accounts[{i}] ({a.get('label', '?')}) 缺少 clientId")
        if self.accounts and self.active_label:
            if not any(a.get("label") == self.active_label for a in self.accounts):
                errs.append(f"active 指向不存在的账户：{self.active_label!r}")
        return errs

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/api/ai-proxy/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/api/api-keys/list"

    def account_by_label(self, label: str) -> dict | None:
        for a in self.accounts:
            if a.get("label") == label:
                return a
        return None

    def active_account(self) -> dict | None:
        if self.active_label:
            hit = self.account_by_label(self.active_label)
            if hit:
                return hit
        for a in self.accounts:
            if a.get("enabled", True):
                return a
        return self.accounts[0] if self.accounts else None

    def save_active(self, label: str) -> None:
        """把切换结果写回配置文件。"""
        data = dict(self._raw)
        data["active"] = label
        self._raw = data
        self.active_label = label
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def load_config(path: Path) -> Config:
    if not path.exists():
        die(f"找不到配置文件：{path}\n"
            f"复制 config.example.json 为 config.json 并填入你的账户信息。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"配置文件 JSON 解析失败：{e}")
    if not isinstance(data, dict):
        die("配置文件根节点必须是对象")
    return Config(path, data)


# ------------------------------------------------------------------ 日志

_VERBOSE = False
_T0 = time.time()


def log(msg: str, *, level: str = "INFO") -> None:
    if _VERBOSE or level != "DEBUG":
        print(f"[{time.time() - _T0:7.2f}s] {level:5} {msg}", flush=True)


def die(msg: str, code: int = 1):
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(code)


# -------------------------------------------------------------- 上游调用

class Upstream:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._models: dict[str, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    def _ssl_ctx(self):
        if self.cfg.verify_tls:
            return ssl.create_default_context()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _open(self, url: str, method: str, body: str | None,
              client_id: str) -> tuple[int, str]:
        req = urllib.request.Request(url, data=body.encode() if body else None,
                                     method=method)
        req.add_header("X-Client-Id", client_id)
        req.add_header("Content-Type", "application/json")
        # 认证头不发送：后端完全忽略 Authorization / JWT。
        ctx = self._ssl_ctx() if url.startswith("https") else None
        with urllib.request.urlopen(req, timeout=self.cfg.request_timeout,
                                    context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")

    # -- 模型目录 -----------------------------------------------------------

    def models(self, client_id: str, force: bool = False) -> tuple[str, list[dict]]:
        with self._lock:
            hit = self._models.get(client_id)
            if hit and not force and time.time() - hit[0] < self.cfg.model_cache_ttl:
                return "ok", hit[1]
        try:
            _, text = self._open(self.cfg.models_url, "GET", None, client_id)
            arr = json.loads(text)
            if not isinstance(arr, list):
                raise ValueError("上游返回的不是数组")
            models = [
                {
                    "id": m.get("title") or m.get("model", ""),
                    "name": m.get("model") or m.get("name", ""),
                    "description": m.get("description", ""),
                }
                for m in arr if isinstance(m, dict)
            ]
            models = [m for m in models if m["id"] and m["name"]]
            with self._lock:
                self._models[client_id] = (time.time(), models)
            log(f"模型目录：{len(models)} 个 (client={client_id})")
            return "ok", models
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            return f"HTTP {e.code}: {body}", []
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}", []

    # -- 聊天 ---------------------------------------------------------------

    def chat(self, account: dict, payload: dict) -> tuple[str, dict | None]:
        obj = dict(payload)
        obj["uuid"] = account["uuid"]
        _, text = self._open(self.cfg.chat_url, "POST", json.dumps(obj),
                             account["clientId"])
        quota = None
        try:
            info = json.loads(text).get("otherInfo")
            if isinstance(info, dict):
                quota = {
                    "total": info.get("totalQuota", 0),
                    "used": info.get("usedQuota", 0),
                    "internalModel": info.get("model", ""),
                }
        except Exception:  # noqa: BLE001
            pass
        return text, quota


# ------------------------------------------------------------------ 服务

class State:
    def __init__(self):
        self.cfg: Config | None = None
        self.upstream: Upstream | None = None
        self.quota: dict[str, dict] = {}
        self.requests = 0


STATE = State()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"mtbridge/{__version__}"

    def log_message(self, fmt, *args):  # noqa: D102
        log(fmt % args, level="DEBUG")

    # -- helpers ------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _err(self, code: int, msg: str):
        self._json(code, {"error": {"message": msg}})

    def _account(self) -> dict | None:
        assert STATE.cfg is not None
        return STATE.cfg.active_account()

    # -- routes -------------------------------------------------------------

    def do_OPTIONS(self):  # noqa: N802
        self._send(204, b"")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/models"):
            self._models()
        elif "/models/" in path:
            self._model(path.rsplit("/models/", 1)[-1])
        elif path.endswith("/status"):
            self._status()
        elif path.endswith("/accounts"):
            self._accounts()
        else:
            self._err(404, "可用端点: /v1/models, /v1/chat/completions, /v1/status, /v1/accounts")

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if not path.endswith("/chat/completions"):
            self._err(404, "仅支持 POST /v1/chat/completions")
            return
        self._chat()

    # -- handlers -----------------------------------------------------------

    def _models(self):
        acc = self._account()
        if acc is None:
            self._err(503, "配置中没有可用账户")
            return
        assert STATE.upstream is not None
        err, models = STATE.upstream.models(acc["clientId"])
        if not models:
            self._err(502, f"无法获取上游模型目录：{err}")
            return
        data = [
            {
                "id": m["id"],
                "object": "model",
                "created": 1788400000,
                "owned_by": "minitavern",
                "name": m["name"],
                "description": m["description"],
            }
            for m in models
        ]
        self._json(200, {"object": "list", "data": data})

    def _model(self, mid: str):
        acc = self._account()
        if acc is None:
            self._err(503, "配置中没有可用账户")
            return
        assert STATE.upstream is not None
        _, models = STATE.upstream.models(acc["clientId"])
        hit = next((m for m in models if m["id"] == mid or m["name"] == mid), None)
        if hit is None:
            self._err(404, f"没有这个模型：{mid}")
            return
        self._json(200, {"id": hit["id"], "object": "model", "created": 1788400000,
                         "owned_by": "minitavern", "name": hit["name"],
                         "description": hit["description"]})

    def _status(self):
        cfg = STATE.cfg
        acc = self._account()
        out: dict[str, Any] = {
            "version": __version__,
            "config": str(cfg.path) if cfg else None,
            "listen": f"{cfg.host}:{self.server.server_address[1]}" if cfg else None,
            "upstream": cfg.base_url if cfg else None,
            "accounts": len(cfg.accounts) if cfg else 0,
            "requests": STATE.requests,
        }
        if acc:
            out["active"] = acc.get("label")
            out["uuid"] = acc.get("uuid")
            out["quota"] = STATE.quota.get(acc["uuid"])
        self._json(200, out)

    def _accounts(self):
        cfg = STATE.cfg
        assert cfg is not None
        active = self._account() or {}
        self._json(200, {
            "active": active.get("label"),
            "accounts": [
                {
                    "label": a.get("label"),
                    "uuid": a.get("uuid"),
                    "enabled": a.get("enabled", True),
                    "quota": STATE.quota.get(a["uuid"]),
                }
                for a in cfg.accounts
            ],
        })

    def _chat(self):
        acc = self._account()
        if acc is None:
            self._err(503, "配置中没有可用账户")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            self._err(400, f"请求体不是合法 JSON：{e}")
            return

        assert STATE.upstream is not None and STATE.cfg is not None
        requested = payload.get("model", "")

        # 短 id → 完整名。后端只认完整名。
        _, models = STATE.upstream.models(acc["clientId"])
        full = next((m["name"] for m in models
                     if m["id"] == requested or m["name"] == requested), None)
        if full:
            payload["model"] = full
        elif "/" not in requested:
            self._err(400, f"未知模型 {requested!r}；请用 /v1/models 里的 id 或完整名")

        STATE.requests += 1
        try:
            text, quota = STATE.upstream.chat(acc, payload)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            log(f"上游 HTTP {e.code}: {detail[:160]}", level="WARN")
            self._err(e.code, detail[:400])
            return
        except Exception as e:  # noqa: BLE001
            log(f"上游异常：{e}", level="ERROR")
            self._err(502, f"{type(e).__name__}: {e}")
            return

        if quota:
            STATE.quota[acc["uuid"]] = quota
            log(f"配额 {quota['used']}/{quota['total']} · {quota['internalModel']}")
        self._send(200, text.encode("utf-8"))


# ------------------------------------------------------------------ 自检

def self_test(cfg: Config) -> int:
    print("\n=== 配置自检 ===")
    errs = cfg.problems()
    for e in errs:
        print(f"  ✗ {e}")
    if not errs:
        print(f"  ✓ 配置合法：{len(cfg.accounts)} 个账户，活动 = {cfg.active_account().get('label')}")
    else:
        return 1

    print("\n=== 网络自检 ===")
    up = Upstream(cfg)
    acc = cfg.active_account()
    err, models = up.models(acc["clientId"])
    if models:
        print(f"  ✓ 上游可达，{len(models)} 个模型")
        for m in models[:5]:
            print(f"      {m['id']:18} {m['name']}")
        if len(models) > 5:
            print(f"      ... 其余 {len(models) - 5} 个")
    else:
        print(f"  ✗ 模型目录获取失败：{err}")
        return 1

    print("\n=== 端口自检 ===")
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((cfg.host, cfg.port))
        print(f"  ✓ {cfg.host}:{cfg.port} 可用")
    except OSError as e:
        print(f"  ✗ {cfg.host}:{cfg.port} 不可用：{e}")
        return 1
    finally:
        probe.close()
    return 0


# ------------------------------------------------------------------ 入口

def main() -> int:
    global _VERBOSE

    ap = argparse.ArgumentParser(
        prog="mtbridge",
        description="MiniTavern 本地代理（OpenAI 兼容端点）",
    )
    ap.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG,
                    help="配置文件路径（默认 ./config.json）")
    ap.add_argument("--check", action="store_true", help="只检查配置与网络后退出")
    ap.add_argument("--test", action="store_true", help="启动服务并执行端点自检")
    ap.add_argument("--active", metavar="LABEL", help="切换活动账户并写回配置")
    ap.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    ap.add_argument("--version", action="version", version=f"mtbridge {__version__}")
    args = ap.parse_args()

    cfg = load_config(args.config)
    _VERBOSE = args.verbose or cfg.verbose

    if args.active:
        if cfg.account_by_label(args.active) is None:
            die(f"没有名为 {args.active!r} 的账户。已有："
                + ", ".join(a.get("label", "?") for a in cfg.accounts))
        cfg.save_active(args.active)
        print(f"活动账户已切换为 {args.active}（已写入 {cfg.path}）")
        return 0

    if args.check:
        return self_test(cfg)

    errs = cfg.problems()
    if errs:
        for e in errs:
            print(f"配置错误：{e}", file=sys.stderr)
        print(f"\n请检查 {cfg.path}", file=sys.stderr)
        return 2

    STATE.cfg = cfg
    STATE.upstream = Upstream(cfg)

    try:
        httpd = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    except OSError as e:
        die(f"无法绑定 {cfg.host}:{cfg.port} — {e}")

    acc = cfg.active_account()
    log(f"mtbridge {__version__}")
    log(f"配置     {cfg.path}")
    log(f"上游     {cfg.base_url}")
    log(f"监听     http://{cfg.host}:{httpd.server_address[1]}/v1")
    log(f"账户     {len(cfg.accounts)} 个，活动 = {acc.get('label') if acc else '无'}")
    log("客户端填 Base URL 即可；API Key 任意（后端不校验）")

    if args.test:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        time.sleep(0.4)
        base = f"http://{cfg.host}:{httpd.server_address[1]}"
        print("\n=== 端点自检 ===")
        for path in ("/v1/models", "/v1/status", "/v1/accounts"):
            try:
                with urllib.request.urlopen(base + path, timeout=20) as r:
                    body = r.read().decode("utf-8", "replace")
                    print(f"  ✓ GET {path:16} HTTP {r.status}  {len(body)} 字节")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ GET {path:16} {e}")
                return 1
        print("\n服务仍在运行，Ctrl+C 停止。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            httpd.shutdown()
        return 0

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("已停止")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
