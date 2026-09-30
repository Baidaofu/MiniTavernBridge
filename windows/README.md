# mtbridge — Windows 版

把 MiniTavern 会员代理包装成 OpenAI 兼容端点。零第三方依赖，只用 Python 标准库。

**两个脚本**：

| 脚本 | 用途 |
|---|---|
| `mtbridge_tui.py` | **日常用这个**。自带终端界面，同时跑代理 |
| `mtbridge.py` | 无界面的纯命令行版（被 TUI 复用，自检也走它） |

安卓版见 [`../android`](../android)（功能一致，MD3 Expressive 界面）。

---

## 快速开始

```bash
# 1. 复制配置模板
copy config.example.json config.json

# 2. 编辑 config.json，填入 uuid 和 clientId

# 3. 自检（配置合法性 + 上游连通性 + 端口占用）
python mtbridge.py --check

# 4. 启动：代理 + 终端界面
python mtbridge_tui.py
```

界面里可切换账户、改名、停用/启用、删除、看配额与最近调用。
详见 [TUI.md](TUI.md)。

不想开界面（跑成后台服务、放脚本里）时用：

```bash
python mtbridge.py            # 等价于 mtbridge_tui.py --headless
```

客户端里填：

```
Base URL   http://127.0.0.1:8787/v1
API Key    任意字符串
Path       /chat/completions
```

---

## 命令

| 命令 | 作用 |
|---|---|
| `python mtbridge_tui.py` | **推荐**。启动代理 + 终端界面 |
| `python mtbridge_tui.py --headless` | 只跑代理不开界面 |
| `python mtbridge_tui.py --port 8899` | 覆盖配置里的端口 |
| `python mtbridge_tui.py --export 账户.json` | 导出账户列表后退出 |
| `python mtbridge_tui.py --import 账户.json` | 导入账户列表后退出（可加 `--replace`） |
| `python mtbridge.py` | 纯命令行启动（无界面） |
| `python mtbridge.py --check` | 配置 / 网络 / 端口自检后退出 |
| `python mtbridge.py --test` | 启动并对各端点发一次真实请求 |
| `python mtbridge.py --active "手机B"` | 切换活动账户并写回配置 |
| `python mtbridge.py -v` | 打开调试日志 |

> TUI 在非交互终端（管道、重定向、CI）下会自动转为 headless。

---

## 配置说明

**所有参数都从 `config.json` 读取，代码里没有写死的地址、端口或凭据。**

```jsonc
{
  "listen": {
    "host": "127.0.0.1",   // 改成 0.0.0.0 可让局域网访问（注意无鉴权）
    "port": 8787
  },
  "upstream": {
    "base": "https://monitor.mini-tavern.com"
  },

  "request_timeout_seconds": 300,   // 上游请求超时
  "model_cache_ttl_seconds": 300,   // 模型目录缓存时长
  "verify_tls": true,               // 关掉可跳过证书校验（自签代理场景）
  "verbose": false,

  "active": "手机B",                 // 当前活动账户的 label

  "accounts": [
    {
      "label": "手机B",              // 唯一标识，用于 --active 切换
      "uuid": "b1d7772c…",          // 必需，账户凭据
      "clientId": "68cd199d…",      // 必需
      "enabled": true               // false 则跳过（用于临时停用某账户）
    }
  ]
}
```

### 怎么拿 `uuid`

用安卓端的 **MiniTavern Bridge** App 点「扫描设备」，它会从 MiniTavern 进程内存里
提取。或者手动从 `token.txt` / 任意一次抓包中取 JWT payload 的 `uuid` 字段。

`clientId` 实测在**所有设备、所有安装上完全相同**，是从 App 本身派生的常量。

---

## 多账户

`accounts` 数组可以有任意多个账户。切换：

```bash
python mtbridge.py --active phoneA
```

切换会写回 `config.json`，服务本身无需重启（每次请求都实时读取）。

也可以直接编辑 `active` 字段，效果相同。

> 每个账户的配额独立计数，实测互不影响。

---

## 端点

| 端点 | 用途 |
|---|---|
| `GET /v1/models` | OpenAI 标准模型列表 |
| `GET /v1/models/{id}` | 单模型查询 |
| `POST /v1/chat/completions` | 聊天 |
| `GET /v1/status` | 服务状态、当前账户、累计请求数 |
| `GET /v1/accounts` | 账户列表及各账户最近配额 |
| `OPTIONS *` | CORS 预检 |

后三个是本程序附加的，方便脚本查询。

---

## 认证模型（实测结论）

| 凭据 | 是否必需 | 说明 |
|---|---|---|
| `X-Client-Id` | **是** | 缺失或错误 → `用户不存在` (401) |
| body `uuid` | **是** | 缺失 → `uuid should not be empty` |
| `Authorization: Bearer <JWT>` | **否** | 后端完全忽略，**本程序不发送** |

第 3 点意味着：

- **客户端不需要填真实 API Key**，随便填即可
- token 过期不影响任何调用
- `uuid` 是唯一的账户凭据，等同密码，**不要外传**

---

## 已知限制

- **流式是“能用但有条件”。** 客户端开 `stream: true` 时：上游若返回
  `text/event-stream` 就逐块原样透传；若只给整包 JSON、或直接拒绝 `stream`
  参数，本程序会本地切成 `chat.completion.chunk` 再下发，因此客户端**总能拿到
  SSE**。反过来客户端要 JSON 而上游给 SSE，会累积拼回一份完整响应。合成流的
  节奏与上游真实生成速度无关（一次性补齐），chunk 固定 64 字符。
- **上下文窗口未知。** 后端不返回该信息，客户端配置需手填。
- **`gpt-5.4` / `gpt-6-astra` 有隐藏注入。** 同样的短请求，这两个模型
  `prompt_tokens` 约 550，而 `deepseek` 只有 9。服务端为这两条线路注入了
  约 540 token 的 system prompt，不是「官方 gpt-5.4」。
- **配额重置时间后端未提供。** `GET /api/users/getAdQuota` 返回
  `{"quota":0,"time":0}`，无实际信息。

---

## 合规提示

本工具绕过 MiniTavern App 的设备签名校验（后端 `/api/auth/app/metrics`
持续统计 `signature_ok` / `signature_fail` / `nonce_replay`）。
**这实质上违反其服务条款，封号风险由使用者自行承担。**

## 配额

后端**没有可用的配额查询接口**——`GET /api/users/getAdQuota` 返回
`{"quota":0,"time":0}` 且带 `errorCode: GET_AD_QUOTA_FAILED`。

配额的唯一来源是任意一次对话响应里的 `otherInfo`：

```json
"otherInfo": { "totalQuota": 100, "usedQuota": 2, "model": "m1" }
```

TUI 的账户表会在每次调用后自动更新，也可以 `GET /v1/accounts` 查各账户配额。

---
