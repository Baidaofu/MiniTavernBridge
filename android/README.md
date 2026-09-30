# MiniTavern Bridge

把 MiniTavern 会员代理包装成 **OpenAI 兼容端点**，让 pi、Kelivo、SillyTavern
等任意客户端都能直接使用。

两个实现，功能一致：

| | [安卓版](android/) | [Windows 版](windows/) |
|---|---|---|
| 语言 | Kotlin / Jetpack Compose | Python（零第三方依赖） |
| 界面 | Material 3 | 命令行 |
| 账户提取 | ✅ root 转储进程内存 | — （手动填 uuid） |
| 本地代理 | ✅ 前台服务 | ✅ |
| 流式 SSE | ✅ 透传 / 本地合成 | ✅ 透传 / 本地合成 |
| 多账户 | ✅ UI 切换 | ✅ `--active` 切换 |
| 导入导出 | ✅ | — |

---

## 为什么需要这个

MiniTavern 后端有三个特性让通用客户端接不上：

| # | 后端要求 | 客户端限制 | 后果 |
|---|---|---|---|
| 1 | `GET /api/ai-proxy/models` 不存在 | 客户端靠它拉模型列表 | 列表永远为空 |
| 2 | 必须带 `X-Client-Id` 头 | 大多支持 | 可解决 |
| 3 | **`uuid` 必须在 JSON body 里** | pi / Kelivo 无法注入 body 字段 | **无法解决** |

第 3 点是死结。实测 8 种 header 名（`X-Uuid`、`X-Device-Uuid`、`X-Client-Uuid`、
`uuid`、`X-Monitor-Uuid`、`X-Device-Id` 等）和 2 种 query 参数（`?uuid=`、
`?deviceUuid=`）**全部无效**，后端一律返回 `uuid should not be empty`。

所以必须在中间垫一层转换。

### 认证模型的实测结论

| 凭据 | 是否必需 | 说明 |
|---|---|---|
| `X-Client-Id` | **是** | 缺失或错误 → `用户不存在` (401)。实测在**所有设备、所有安装上完全相同**，是从 App 派生的常量 |
| body `uuid` | **是** | **唯一的账户凭据**，等同密码 |
| `Authorization: Bearer <JWT>` | **否** | 后端**完全忽略**。有效值、垃圾值、不传，三者行为一致 |

因此客户端 **API Key 随便填**，token 过期也不影响任何调用。

---

## 快速开始

### Windows

```bash
cd windows
copy config.example.json config.json    # 填入 uuid / clientId
python mtbridge.py --check              # 自检
python mtbridge.py                      # 启动
```

### 安卓

```bash
cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

需要 root。启动后点「扫描设备」→「启动代理」。

### 客户端配置（两边一致）

```
Base URL   http://127.0.0.1:8787/v1
API Key    任意字符串
Path       /chat/completions
```

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

返回 24 个模型。**不带 `X-Client-Id` 会返回空数组**（容易误判成「无模型接口」）。

响应里 `title` 是内部短 id（`m1`、`tuzi-vision13`），`model` 是后端要求的完整名。
后端**只认完整名**，传短 id 会报 `model_not_found`。两个实现都做了自动转换。

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

---

## 安全

`uuid` 等同于账户密码。

`.gitignore` 已排除 `config.json`、`token*.txt`、`local.properties` 等敏感文件。
**导出/复制配置时请勿外传。**

---

## 合规提示

本工具绕过 MiniTavern App 的设备签名校验（后端 `/api/auth/app/metrics`
持续统计 `signature_ok` / `signature_fail` / `nonce_replay`）。
**这实质上违反其服务条款，封号风险由使用者自行承担。**
