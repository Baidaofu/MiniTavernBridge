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
import base64
import json
import os
import secrets
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

# 从 App 派生的常量，所有安装上相同；后端缺失该头会返回「用户不存在」
DEFAULT_CLIENT_ID = "68cd199d61947054fdf25ebe"

# 实测在所有设备、所有安装上完全相同，是 App 派生的常量
DEFAULT_CLIENT_ID = "68cd199d61947054fdf25ebe"


# --------------------------------------------------------------------- 配置

class Config:
    """运行时配置。所有字段都来自 JSON，缺省值仅用于报错提示。"""

    def __init__(self, path: Path, data: dict):
        self.path = path
        self._raw = data
        self._mtime = self._stat()
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

    def _stat(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def refresh_if_changed(self) -> bool:
        """配置文件被外部改动（--import_、手动编辑）时重新读入。

        服务只在启动时加载一次配置，不刷新的话，运行中导入的账户
        要重启才能生效。mtime 比对很便宜，每次请求前做一次即可。
        """
        m = self._stat()
        if m == self._mtime:
            return False
        self._mtime = m
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        self._raw = data
        listen = data.get("listen", {})
        upstream = data.get("upstream", {})
        self.host = listen.get("host", self.host)
        self.port = int(listen.get("port", self.port))
        self.base_url = upstream.get("base", self.base_url).rstrip("/")
        self.accounts = data.get("accounts", [])
        self.active_label = data.get("active", "")
        return True

    # -- 校验 ---------------------------------------------------------------

    def problems(self) -> list[str]:
        """阻塞性错误。账户为空**不算错误** —— 没有账户时服务照常启动，
        只是 /v1/* 会返回 503，可用 --import_ 导入后再切账户。"""
        errs: list[str] = []
        if not self.base_url:
            errs.append('缺少 upstream.base')
        elif not self.base_url.startswith(("http://", "https://")):
            errs.append(f"upstream.base 必须以 http:// 或 https:// 开头：{self.base_url}")
        if self.port <= 0 or self.port > 65535:
            errs.append(f"listen.port 非法：{self.port}")
        for i, a in enumerate(self.accounts):
            if not a.get("uuid"):
                errs.append(f"accounts[{i}] ({a.get('label', '?')}) 缺少 uuid")
            if not a.get("clientId"):
                errs.append(f"accounts[{i}] ({a.get('label', '?')}) 缺少 clientId")
        if self.accounts and self.active_label:
            if not any(a.get("label") == self.active_label for a in self.accounts):
                errs.append(f"active 指向不存在的账户：{self.active_label!r}")
        return errs

    def warnings(self) -> list[str]:
        """非阻塞提醒。"""
        warn: list[str] = []
        if not self.accounts:
            warn.append(
                "accounts 为空：服务可以启动，但 /v1/* 会返回 503。"
                "用 `python mtbridge_tui.py --import_ <文件>` 导入账户列表，"
                "或直接编辑 config.json。"
            )
        return warn

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
        self.refresh_if_changed()
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
        self._raw["active"] = label
        self.active_label = label
        self.save()

    def save(self) -> None:
        """把内存中的 accounts / active 写回配置文件。"""
        self._raw["accounts"] = self.accounts
        self._raw["active"] = self.active_label
        _write_json(self.path, self._raw)
        self._mtime = self._stat()

    def export_json(self) -> str:
        """账户列表导出（不含任何服务端凭据，仅 uuid/clientId/备注）。"""
        data = {
            "format": "mtbridge-accounts",
            "version": 1,
            "exportedAt": int(time.time() * 1000),
            "count": len(self.accounts),
            "accounts": [
                {
                    "label": a.get("label", ""),
                    "uuid": a.get("uuid", ""),
                    "clientId": a.get("clientId", ""),
                    "note": a.get("note", ""),
                    "enabled": a.get("enabled", True),
                    "quotaTotal": a.get("quotaTotal", 0),
                    "quotaUsed": a.get("quotaUsed", 0),
                }
                for a in self.accounts
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    def import_json(self, raw: str, replace: bool = False) -> tuple[int, int]:
        """导入账户，返回 (新增, 跳过)。按 uuid 去重。"""
        root = json.loads(raw)
        arr = root.get("accounts") if isinstance(root, dict) else root
        if not isinstance(arr, list):
            raise ValueError("找不到 accounts 数组")
        if replace:
            self.accounts = []
        existing = {a.get("uuid") for a in self.accounts}
        added = skipped = 0
        for item in arr:
            if not isinstance(item, dict):
                continue
            u = (item.get("uuid") or "").strip()
            if len(u) < 16 or u in existing:
                skipped += 1
                continue
            existing.add(u)
            self.accounts.append({
                "label": item.get("label") or f"账户 {u[:6]}",
                "uuid": u,
                "clientId": (item.get("clientId") or "").strip()
                            or DEFAULT_CLIENT_ID,
                "note": item.get("note", ""),
                "enabled": item.get("enabled", True),
            })
            added += 1
        if not self.active_label and self.accounts:
            # 导入后没有指定 active，自动选第一个，否则看起来像「导入了
            # 但服务仍然认不出账户」
            self.active_label = self.accounts[0].get("label", "")
        self.save()
        return added, skipped


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


#: config.json 不存在时自动生成的骨架。所有地址/端口都有可用的默认值，
#: 账户留空 —— 有账户时才能发请求。
CONFIG_TEMPLATE: dict = {
    "listen": {"host": "127.0.0.1", "port": 8787},
    "upstream": {"base": "https://monitor.mini-tavern.com"},
    "request_timeout_seconds": 300,
    "model_cache_ttl_seconds": 300,
    "verify_tls": True,
    "verbose": False,
    "active": "",
    "accounts": [],
}


def ensure_config(path: Path) -> bool:
    """没有配置文件就生成一个空骨架。返回是否新建了。"""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, CONFIG_TEMPLATE)
    return True


def load_config(path: Path) -> Config:
    if not path.exists():
        ensure_config(path)
        print(f"已生成空配置：{path}")
        print("填入 uuid 即可使用，或用 --import_ 导入账户列表。\n")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"配置文件 JSON 解析失败：{e}")
    if not isinstance(data, dict):
        die("配置文件根节点必须是对象")
    cfg = Config(path, data)
    if not path.exists() or not data.get("upstream"):
        # 自动生成的骨架没有自定义上游，补上默认值再落盘
        data.setdefault("upstream", CONFIG_TEMPLATE["upstream"])
        _write_json(path, data)
    return cfg


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

    def open_chat(self, account: dict, payload: dict):
        """打开一次 chat 调用，返回 (status, content_type, response)。

        response 的 body 还没读，调用方可以逐块读取上游 SSE；用完必须 close。
        非 2xx 仍由 urllib 抛 HTTPError，交给调用方处理。
        """
        obj = dict(payload)
        obj["uuid"] = account["uuid"]
        req = urllib.request.Request(self.cfg.chat_url,
                                     data=json.dumps(obj).encode("utf-8"),
                                     method="POST")
        req.add_header("X-Client-Id", account["clientId"])
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream, application/json"
                       if obj.get("stream") else "application/json")
        # 认证头不发送：后端完全忽略 Authorization / JWT。
        ctx = self._ssl_ctx() if self.cfg.chat_url.startswith("https") else None
        resp = urllib.request.urlopen(req, timeout=self.cfg.request_timeout,
                                      context=ctx)
        ctype = (resp.headers.get_content_type() or "").lower()
        return resp.status, ctype, resp


# ------------------------------------------------------------------ 服务

class State:
    def __init__(self):
        self.cfg: Config | None = None
        self.upstream: Upstream | None = None
        self.quota: dict[str, dict] = {}
        self.requests = 0


STATE = State()


# ---------------------------------------------------------- 流式辅助工具

def _quota_from(info: dict) -> dict:
    return {
        "total": info.get("totalQuota", 0),
        "used": info.get("usedQuota", 0),
        "internalModel": info.get("model", ""),
    }


def parse_quota(text: str) -> dict | None:
    """从一份完整 JSON 或一段 SSE 事件里找出 otherInfo 配额。"""
    def scan(blob: str) -> dict | None:
        try:
            obj = json.loads(blob)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(obj, dict) and isinstance(obj.get("otherInfo"), dict):
            return _quota_from(obj["otherInfo"])
        return None

    hit = scan(text)
    if hit:
        return hit
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]" or not payload.startswith("{"):
            continue
        hit = scan(payload)
        if hit:
            return hit
    return None


def split_pieces(text: str, size: int = 64) -> list[str]:
    """切片。Python 的 str 按码点索引，不会切开代理对。"""
    return [text[i:i + size] for i in range(0, len(text), size)] or []


# ------------------------------------------------------ 调试账户（默认隐藏）

def provision_test_account(cfg: Config) -> dict:
    """凭空造一个可用的测试账户并返回它的配置项。

    `POST /api/auth/app/bootstrap` 就是 MiniTavern 首次安装时的自注册入口，
    对任意 uuid 都会开户并签发 accessToken，随后按账户发放免费配额。
    实测随机 uuid 不调这一步、直接 chat 也会被自动开户，所以 bootstrap
    失败会退化成"只造 uuid"。

    用途：手边没有多余真机账户时验证链路（配额、模型、多账户切换、
    配额耗尽后的错误路径）。

    ⚠️ 这是服务端真实开户动作。单个用于本地调试没问题，批量生成属于滥用，
    请勿大量使用。
    """
    up = Upstream(cfg)
    uuid = secrets.token_hex(32)

    token = ""
    try:
        url = f"{cfg.base_url}/api/auth/app/bootstrap"
        req = urllib.request.Request(url, data=json.dumps({"uuid": uuid}).encode())
        req.add_header("X-Client-Id", DEFAULT_CLIENT_ID)
        req.add_header("Content-Type", "application/json")
        ctx = up._ssl_ctx() if url.startswith("https") else None
        with urllib.request.urlopen(req, timeout=cfg.request_timeout,
                                    context=ctx) as resp:
            blob = json.loads(resp.read().decode("utf-8", "replace"))
        token = (blob.get("data") or {}).get("accessToken") or ""
    except Exception as e:  # noqa: BLE001
        log(f"bootstrap 未成功（继续，直接用新 uuid）: {e}", level="WARN")

    sub = ""
    if token:
        try:
            part = token.split(".")[1]
            sub = json.loads(base64.urlsafe_b64decode(
                part + "=" * (-len(part) % 4)))["sub"]
        except Exception:  # noqa: BLE001
            sub = ""

    # 探一次配额：既验证新账户真能调通，也把初始配额读回来
    quota = None
    try:
        _, models = up.models(DEFAULT_CLIENT_ID)
        if models:
            code, text = up._open(cfg.chat_url, "POST", json.dumps({
                "uuid": uuid, "model": models[0]["name"],
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }), DEFAULT_CLIENT_ID)
            if code == 200:
                quota = parse_quota(text)
    except Exception as e:  # noqa: BLE001
        log(f"配额探测失败: {e}", level="WARN")

    return {
        "label": f"调试 {uuid[:6]}",
        "uuid": uuid,
        "clientId": DEFAULT_CLIENT_ID,
        "sub": sub,
        "token": token,
        "tokenExp": 0,
        "enabled": True,
        "quotaTotal": (quota or {}).get("total", 0),
        "quotaUsed": (quota or {}).get("used", 0),
    }


def add_test_account(cfg: Config) -> dict:
    """造一个测试账户、写进配置并设为活动账户。返回该账户。"""
    acc = provision_test_account(cfg)
    cfg.accounts = [a for a in cfg.accounts if a.get("uuid") != acc["uuid"]]
    cfg.accounts.append(acc)
    cfg.active_label = acc["label"]
    cfg.save()
    return acc


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
                # id 用后端真实全名，第三方软件直接看到可读模型名；
                # 短 id 仍可用于发请求，额外挂在 mtb_id 上。
                "id": m["name"],
                "object": "model",
                "created": 1788400000,
                "owned_by": "minitavern",
                "name": m["name"],
                "mtb_id": m["id"],
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
        self._json(200, {"id": hit["name"], "object": "model", "created": 1788400000,
                         "owned_by": "minitavern", "name": hit["name"],
                         "mtb_id": hit["id"], "description": hit["description"]})

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
        want_stream = bool(payload.get("stream"))

        try:
            _, ctype, resp = STATE.upstream.open_chat(acc, payload)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if not want_stream:
                log(f"上游 HTTP {e.code}: {detail[:160]}", level="WARN")
                self._err(e.code, detail[:400])
                return
            # 客户端要流式，上游可能根本不认 stream 参数：去掉标志重试，
            # 成功后由本地把整包 JSON 切成 SSE。
            log(f"上游拒绝 stream（HTTP {e.code}），回退非流式并本地切片："
                f"{detail[:120]}", level="WARN")
            payload.pop("stream", None)
            try:
                _, ctype, resp = STATE.upstream.open_chat(acc, payload)
            except urllib.error.HTTPError as e2:
                d2 = e2.read().decode("utf-8", "replace")
                log(f"上游 HTTP {e2.code}: {d2[:160]}", level="WARN")
                self._err(e2.code, d2[:400])
                return
            except Exception as e2:  # noqa: BLE001
                log(f"上游异常：{e2}", level="ERROR")
                self._err(502, f"{type(e2).__name__}: {e2}")
                return
        except Exception as e:  # noqa: BLE001
            log(f"上游异常：{e}", level="ERROR")
            self._err(502, f"{type(e).__name__}: {e}")
            return

        with resp:
            if want_stream and ctype == "text/event-stream":
                self._relay_sse(resp, acc)          # ① 上游真流式 → 透传
            elif want_stream:
                text = resp.read().decode("utf-8", "replace")
                self._synthetic_sse(text, acc, payload.get("model", ""))
            elif ctype == "text/event-stream":
                text = resp.read().decode("utf-8", "replace")
                self._accumulate_sse(text, acc, payload.get("model", ""))
            else:
                text = resp.read().decode("utf-8", "replace")
                self._note_quota(acc, parse_quota(text))
                self._send(200, text.encode("utf-8"))

    # -- 流式实现 -----------------------------------------------------------

    def _note_quota(self, acc: dict, quota: dict | None):
        if quota:
            STATE.quota[acc["uuid"]] = quota
            log(f"配额 {quota['used']}/{quota['total']} · {quota['internalModel']}")

    def _sse_head(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.close_connection = True

    def _sse_send(self, data: bytes):
        """写一个 HTTP chunk 并立即 flush —— 客户端才能逐块收到。"""
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
        self.wfile.flush()

    def _sse_end(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _relay_sse(self, resp, acc: dict):
        """上游就是 SSE：原样逐事件透传。"""
        log("上游流式响应，逐块透传")
        self._sse_head()
        event: list[str] = []
        quota = None
        saw_done = False

        def flush():
            nonlocal event, quota, saw_done
            if not event:
                return
            blob = "\n".join(event) + "\n\n"
            self._sse_send(blob.encode("utf-8"))
            quota = parse_quota(blob) or quota
            if "[DONE]" in blob:
                saw_done = True
            event = []

        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line:
                event.append(line)
                continue
            flush()
        flush()
        # 上游实测不发 data: [DONE] 就断开，严格按 OpenAI 语义的客户端会等到超时
        if not saw_done:
            self._sse_send(b"data: [DONE]\n\n")
        self._sse_end()
        self._note_quota(acc, quota)

    def _synthetic_sse(self, text: str, acc: dict, model: str):
        """上游只给整包 JSON：本地切成 OpenAI 风格的 chat.completion.chunk。"""
        log("上游返回整包 JSON，本地合成 SSE 流")
        self._note_quota(acc, parse_quota(text))

        try:
            root = json.loads(text)
        except Exception:  # noqa: BLE001
            root = None

        cid = "chatcmpl-mtb"
        created = int(time.time())
        out_model = model
        usage = None
        contents: dict[int, str] = {}
        finishes: dict[int, str] = {}
        if isinstance(root, dict):
            cid = root.get("id") or cid
            created = root.get("created") or created
            out_model = root.get("model") or out_model
            usage = root.get("usage")
            for i, c in enumerate(root.get("choices") or []):
                if not isinstance(c, dict):
                    continue
                idx = c.get("index", i)
                msg = c.get("message") or {}
                content = (msg.get("content") or c.get("text") or text)
                contents[idx] = content
                finishes[idx] = c.get("finish_reason") or "stop"
        if not contents:
            # 结构不认识：把整包文本当一个 delta 交出去，客户端至少能收到内容
            contents[0] = text
            finishes[0] = "stop"

        self._sse_head()

        def emit(choices: list[dict]):
            obj = {"id": cid, "object": "chat.completion.chunk",
                   "created": created, "model": out_model, "choices": choices}
            blob = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            self._sse_send(f"data: {blob}\n\n".encode("utf-8", "replace"))

        # ① 首包：role
        emit([{"index": i, "delta": {"role": "assistant", "content": ""},
               "finish_reason": None} for i in contents])
        # ② 正文：多 choice 同步推进
        pieces = {i: split_pieces(v) for i, v in contents.items()}
        rounds = max((len(v) for v in pieces.values()), default=0)
        for r in range(rounds):
            arr = [{"index": i, "delta": {"content": lst[r]}, "finish_reason": None}
                   for i, lst in pieces.items() if r < len(lst)]
            if arr:
                emit(arr)
        # ③ 收尾
        emit([{"index": i, "delta": {}, "finish_reason": finishes[i]}
              for i in contents])
        # ④ usage 单独一包（choices 为空），与 OpenAI 语义一致
        if isinstance(usage, dict):
            obj = {"id": cid, "object": "chat.completion.chunk",
                   "created": created, "model": out_model,
                   "choices": [], "usage": usage}
            blob = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            self._sse_send(f"data: {blob}\n\n".encode("utf-8", "replace"))

        self._sse_send(b"data: [DONE]\n\n")
        self._sse_end()

    def _accumulate_sse(self, raw: str, acc: dict, model: str):
        """客户端要 JSON、上游却给 SSE：把增量重新拼回一份完整响应。"""
        if "data:" not in raw:
            # 声称是 SSE 却不是 → 当普通 JSON 交出去
            self._note_quota(acc, parse_quota(raw))
            self._send(200, raw.encode("utf-8"))
            return
        log("客户端要非流式，上游给 SSE，累积后返回")

        cid, created, out_model = "chatcmpl-mtb", None, ""
        finish = "stop"
        usage = other_info = None
        contents: dict[int, list[str]] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]" or not payload.startswith("{"):
                continue
            try:
                obj = json.loads(payload)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(obj, dict):
                continue
            cid = obj.get("id") or cid
            created = created or obj.get("created")
            out_model = obj.get("model") or out_model
            if isinstance(obj.get("usage"), dict):
                usage = obj["usage"]
            if isinstance(obj.get("otherInfo"), dict):
                other_info = obj["otherInfo"]
            for i, c in enumerate(obj.get("choices") or []):
                if not isinstance(c, dict):
                    continue
                idx = c.get("index", i)
                if c.get("finish_reason"):
                    finish = c["finish_reason"]
                delta = c.get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    contents.setdefault(idx, []).append(piece)

        resp: dict[str, Any] = {
            "id": cid,
            "object": "chat.completion",
            "created": created or int(time.time()),
            "model": out_model or model,
            "choices": [
                {"index": i, "message": {"role": "assistant",
                                          "content": "".join(parts)},
                 "finish_reason": finish}
                for i, parts in (contents or {0: [""]}).items()
            ],
        }
        if usage:
            resp["usage"] = usage
        if other_info:
            resp["otherInfo"] = other_info
        self._note_quota(acc, _quota_from(other_info) if other_info else None)
        self._send(200, json.dumps(resp, ensure_ascii=False).encode("utf-8"))


# ------------------------------------------------------------------ 自检

def self_test(cfg: Config) -> int:
    print("\n=== 配置自检 ===")
    errs = cfg.problems()
    for e in errs:
        print(f"  ✗ {e}")
    for w in cfg.warnings():
        print(f"  ! {w}")
    if errs:
        return 1
    acc = cfg.active_account()
    if acc:
        print(f"  ✓ 配置合法：{len(cfg.accounts)} 个账户，活动 = {acc.get('label')}")
    else:
        print("  ✓ 配置合法：0 个账户（服务可启动，调用前需先导入或添加）")

    print("\n=== 网络自检 ===")
    acc = cfg.active_account()
    if acc is None:
        print("  - 跳过：没有活动账户，无法鉴权。")
        print("    先导入账户（--import_ <文件>）或编辑 config.json 再跑一次 --check。")
    else:
        up = Upstream(cfg)
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
    # 调试入口：默认不出现在 --help 里，用 help=argparse.SUPPRESS 隐藏
    ap.add_argument("--test-account", action="store_true",
                    help=argparse.SUPPRESS)
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

    if args.test_account:
        acc = add_test_account(cfg)
        q = f"{acc['quotaUsed']}/{acc['quotaTotal']}" if acc["quotaTotal"] else "未知"
        print(f"已创建调试账户 {acc['label']}（uuid {acc['uuid'][:12]}…，配额 {q}）")
        print(f"已写入 {cfg.path} 并设为活动账户")
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
