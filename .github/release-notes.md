# MiniTavern Bridge

**当前版本 v{{VERSION}}**

把 MiniTavern 会员代理包装成 **OpenAI 兼容端点**，让 pi、Kelivo、SillyTavern
等任意客户端直接使用。

> 本工具绕过 App 的设备签名校验，**违反上游服务条款，封号风险自负**。
> 仅供个人学习与研究。

---

## 附件

| 文件 | 平台 | 说明 |
|---|---|---|
| `mtbridge-{{VERSION}}.apk` | Android | 已用项目签名密钥签名的正式包 |
| `mtbridge-windows.zip` | Windows / macOS / Linux | 纯 Python，零第三方依赖 |

---

## Android

**要求**：root、已安装并登录 MiniTavern。

1. 安装 `mtbridge-{{VERSION}}.apk`
2. 打开 App，进 **账户** 页 → 点「扫描设备」
   （App 会从 MiniTavern 进程内存里提取会话凭据；也可手动填 uuid）
3. 回到**首页** → 点「启动代理」
4. 客户端填：

```
Base URL   http://127.0.0.1:8787/v1
API Key    任意字符串
Path       /chat/completions
```

> 同一台设备上只能跑一个实例（均监听 8787），两个都装时后启动的会启动失败。

---

## Windows / macOS / Linux

**要求**：Python 3.10+（Windows 建议用 [Python 官网](https://www.python.org/downloads/) 安装，
勾选 *Add Python to PATH*）。

解压后：

```bash
# Windows
copy config.example.json config.json
# macOS / Linux
# cp config.example.json config.json
```

编辑 `config.json`，填入 `uuid` 与 `clientId`（uuid 可用安卓端扫出来后复制）。

```bash
python mtbridge.py --check      # 自检：配置 / 上游连通性 / 端口占用
python mtbridge_tui.py          # 启动代理 + 终端界面
```

不想开界面（跑成后台服务）：

```bash
python mtbridge.py              # 等价于 mtbridge_tui.py --headless
```

客户端配置与 Android 相同。

**TUI 快捷键**

| 键 | 作用 |
|---|---|
| `↑` `↓` / `j` `k` | 选择账户 |
| `Enter` / `Space` | 设为活动账户 |
| `e` | 启用 / 停用 |
| `d` | 删除 |
| `R` | 刷新模型目录 |
| `q` / `Ctrl-C` | 退出 |

---

## 常见问题

**客户端拉不到模型列表？**
代理的 `/v1/models` 会自动补齐后端缺失的接口，`id` 用真实全名
（如 `deepseek/deepseek-v3.2-exp`），短 id（`m1`）放在 `mtb_id` 字段。

**流式输出会卡顿？**
后端本身是攒一批 token 再突发下发，节奏由上游决定，本地代理不额外缓冲。
两个实现都支持 SSE：上游给流就透传，上游只给整包 JSON 就本地切片。

**配额怎么查？**
后端没有可用的配额查询接口，配额来自任意一次对话响应的 `otherInfo` 字段。
Android 首页配额卡片、Windows TUI 账户表都会自动读取。

**返回 400 `insufficient_quota`？**
该账户配额已耗尽，换个账户或等后端发放。

---

完整说明见 [仓库 README](https://github.com/Baidaofu/MiniTavernBridge)。
