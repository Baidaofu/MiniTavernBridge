# MiniTavern Bridge

把 MiniTavern 会员代理包装成 **OpenAI 兼容端点**，让只能设置 Base URL / 请求头的
客户端（如 pi）也能直接使用。Kelivo、SillyTavern / TauriTavern 支持自定义请求体，
可以不用本工具直连上游，见 [直连上游](#直连上游不用本工具)。

| | [android/](android/) | [windows/](windows/) |
|---|---|---|
| 语言 | Kotlin / Jetpack Compose | Python（零第三方依赖） |
| 界面 | Material 3（MD3 Expressive 形状） | 终端 TUI / 命令行 |
| 账户提取 | ✅ root 转储进程内存 | — （手动填 uuid） |
| 本地代理 | ✅ 前台服务 | ✅ |
| 流式 SSE | ✅ 透传 / 本地合成 | ✅ 透传 / 本地合成 |
| 多账户 | ✅ UI 切换 | ✅ TUI 切换 / `--active` |
| 导入导出 | ✅ | ✅ |
| 结构化日志 | ✅ 每次调用完整记录 | ✅ TUI 面板 |

---

## 为什么需要这个

MiniTavern 后端有三个特性让通用客户端接不上：

| # | 后端要求 | 客户端限制 | 后果 |
|---|---|---|---|
| 1 | `GET /api/ai-proxy/models` 不存在 | 客户端靠它拉模型列表 | 列表永远为空 |
| 2 | 必须带 `X-Client-Id` 头 | 大多支持 | 可解决 |
| 3 | **`uuid` 必须在 JSON body 里** | pi 只能设 header，不能注入 body | **pi 无法解决**；Kelivo / SillyTavern 可以直连 |

第 3 点对 pi 是死结。实测 8 种 header 名（`X-Uuid`、`X-Device-Uuid`、`X-Client-Uuid`、
`uuid`、`X-Monitor-Uuid`、`X-Device-Id` 等）和 2 种 query 参数（`?uuid=`、
`?deviceUuid=`）**全部无效**，后端一律返回 `uuid should not be empty`。

Kelivo、SillyTavern / TauriTavern 自带 provider 级自定义 body 与 header，不需要这层
转换，配置见 [直连上游](#直连上游不用本工具)。其余客户端在中间垫一层转换即可。

### 认证模型的实测结论

| 凭据 | 是否必需 | 说明 |
|---|---|---|
| `X-Client-Id` | **是** | 缺失或错误 → `用户不存在` (401)。在**三台不同设备的不同安装上实测完全相同**，是从 App 派生的常量 |
| body `uuid` | **是** | **唯一的账户凭据**，等同密码 |
| `Authorization: Bearer <JWT>` | **否** | 后端**完全忽略**。有效值、垃圾值、不传，三者行为一致 |

因此客户端 **API Key 随便填**，token 过期也不影响任何调用。

---

## 快速开始

### Windows

带终端界面的版本（推荐），零第三方依赖：

```bash
cd windows
copy config.example.json config.json    # 填入 uuid / clientId
python mtbridge.py --check              # 自检
python mtbridge_tui.py                   # 启动代理 + TUI
```

界面里可切换账户、改名、停用、导入导出。不想开界面（比如跑成服务）时：

```bash
python mtbridge.py                      # 纯命令行，等价于 TUI 的 --headless
```

详见 [windows/TUI.md](windows/TUI.md)。

### 安卓

需要 root。从 [Releases](https://github.com/Baidaofu/MiniTavernBridge/releases) 下 APK
安装，或自己构建：

```bash
cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

启动后在**账户页**点「扫描设备」提取账户（已从首页移过来），再到首页点「启动代理」。

<details>
<summary>在 arm64 / Termux 上构建？</summary>

Gradle 默认从 Maven 拉的 `aapt2` 是 x86_64 二进制，arm64 上会报
`Failed to start AAPT2 process`。改用系统自带的 aapt2 即可：

```properties
# ~/.gradle/gradle.properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
```
</details>

### 客户端配置（两边一致）

```
Base URL   http://127.0.0.1:8787/v1
API Key    任意字符串
Path       /chat/completions
```

端口在配置文件里改（安卓端固定 8787）。**注意同一台设备上只能跑一个实例**，
两个都监听 8787 时后启动的那个会启动失败。

---

## 直连上游（不用本工具）

Kelivo 和 SillyTavern / TauriTavern 支持在 provider 级别注入自定义 body 和 header，
所以可以绕过本工具直接访问上游。上游本身就是标准 OpenAI 流式接口：

```
POST https://monitor.mini-tavern.com/api/ai-proxy/chat/completions
Header  X-Client-Id: <clientId>     # 必需
Body    uuid: <uuid>                # 必需
Authorization: Bearer <任意>        # 后端忽略
```

`stream: true` 返回真正的 SSE（`chat.completion.chunk`），结尾没有 `data: [DONE]`，
常见客户端都能正常收尾。根路径下没有 `/models`，模型列表只能手动填**完整名**。

### Kelivo

provider 里填：

| 字段 | 值 |
|---|---|
| API Base URL | `https://monitor.mini-tavern.com/api/ai-proxy` |
| API 路径 | `/chat/completions` |
| Response API (/responses) | 关 |
| API Key | 任意 |
| 自定义请求 → Header | `X-Client-Id: <clientId>` |
| 自定义请求 → Body | `uuid: "<uuid>"`（只留这一条） |
| 模型 | 完整名，如 `deepseek/deepseek-v3.2-exp` |

### SillyTavern / TauriTavern

聊天补全源选 `Custom (OpenAI-compatible)`：

| 字段 | 值 |
|---|---|
| Custom Endpoint (Base URL) | `https://monitor.mini-tavern.com/api/ai-proxy` |
| Custom API Key | 任意 |
| Enter a Model ID | `deepseek/deepseek-v3.2-exp` |
| Additional Parameters → Include Request Headers | `X-Client-Id: <clientId>` |
| Additional Parameters → Include Body Parameters | `uuid: "<uuid>"` |

服务端会把 Base URL 和 `/chat/completions` 拼接，所以 **Base URL 不要带后缀**。

> `uuid` 建议加引号，避免全数字时被 YAML/JSON 解析成数字。

---

## 账户 uuid 从哪来

MiniTavern 把本地数据全部加密：

```
files/settings/apiSettings.json   OpenSSL "Salted__" AES
files/users/userInfo.json          同上
shared_prefs/SecureStore.xml       EncryptedSharedPreferences
```

AES 口令在 Android Keystore 里，**不可导出**，静态读文件走不通。

安卓版的做法是转储运行中进程的内存：解析 `/proc/<pid>/maps` 挑出
`anon:hades-segment` / `anon:hermes-rt`（Hermes GC 堆），`dd` 导出后在
UTF-16 文本中正则匹配 JWT，Base64 解出 payload 取 `uuid`。

> Hermes 把 JS 字符串存为 UTF-16，这是能扫到明文的原因。

Windows 版需要手动填 —— 可以用安卓端扫出来后复制。

---

## 模型目录

上游真实接口是：

```
GET https://monitor.mini-tavern.com/api/api-keys/list
Header: X-Client-Id: <clientId>
```

返回 26 个模型（数量会变）。**不带 `X-Client-Id` 会返回空数组**（容易误判成「无模型接口」）。

响应里 `title` 是内部短 id（`m1`、`tuzi-vision13`），`model` 是后端要求的完整名。
后端**只认完整名**，传短 id 会报 `model_not_found`。两个实现都做了自动转换。

上游这一个接口里**两种名字都给了**（`title` 短 id、`name`/`model` 全名），
所以不经过本工具也能直接查：

```bash
curl -s https://monitor.mini-tavern.com/api/api-keys/list \
     -H "X-Client-Id: <clientId>"
```

`GET /v1/models` 的 `id` 用**真实全名**（如 `deepseek/deepseek-v3.2-exp`），
短 id 放在 `mtb_id` 字段；发请求时两种写法都接受。

---

## 已知限制

- **流式是“能用但有条件”。** 客户端开 `stream: true` 后：上游若返回
  `text/event-stream` 就逐块原样透传；若只给整包 JSON、或直接拒绝 `stream` 参数，
  代理会本地切成 `chat.completion.chunk` 再下发，因此客户端**总能拿到 SSE**。
  反向也处理了：客户端要 JSON 而上游给了 SSE，会累积拼回一份完整响应。
  注意合成流的节奏与上游真实生成速度无关（一次性补齐），且 chunk 大小固定 64 字符。
- **上下文窗口未知**，客户端配置需手填。
- **`gpt-5.4` / `gpt-6-astra` 有隐藏注入。** 同样短请求，这两个模型
  `prompt_tokens` 约 550，而 `deepseek` 只有 9、`gemini` 约 44。服务端为这两条
  线路注入了约 540 token 的 system prompt，**不是「官方 gpt-5.4」**。
- **配额重置时间后端未提供。** `GET /api/users/getAdQuota` 返回 `{"quota":0,"time":0}`。
- **配额按账户独立**，多账户各 100，但都是免费试用额度，实测 `subscriptionExpiry`
  约 28 天后到期，到期后行为未知。
- **安卓版需要 root**，且 MiniTavern 必须处于登录状态。

---

## 配额

后端**没有可用的配额查询接口**——`GET /api/users/getAdQuota` 返回
`{"quota":0,"time":0}` 且带 `errorCode: GET_AD_QUOTA_FAILED`。

配额的唯一来源是**任意一次对话响应里的 `otherInfo`**：

```json
"otherInfo": { "totalQuota": 100, "usedQuota": 2, "model": "m1" }
```

所以查询手段是发一个 `max_tokens: 1` 的极小请求再读 `otherInfo`。
安卓首页的配额卡片、windows TUI 的账户表都会自动这么做。

---

## 合规提示

本工具绕过 MiniTavern App 的设备签名校验（后端 `/api/auth/app/metrics`
持续统计 `signature_ok` / `signature_fail` / `nonce_replay`）。
**这实质上违反其服务条款，封号风险由使用者自行承担。**

仅供个人学习与研究。
