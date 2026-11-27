# MiniTavern Bridge

把 MiniTavern 的聊天接口包装成本地 OpenAI 兼容代理，让只能填写 Base URL 的客户端也能调用。
仓库里是 Android 和 Windows 两个独立实现，互不依赖。

---

## 使用门槛（先看这里）

这不是开箱即用的通用工具，**仓库不提供任何账户**。如果你不满足下面对应条件，
这里的程序对你没有可用价值，可以不用继续往下看。

| 端 | 必须条件 |
|---|---|
| **Android APK** | 设备**已 root**，且 MiniTavern App 处于登录状态。账户 uuid 靠转储运行中进程内存提取。|
| **Windows** | 你已经持有**自己账户的 uuid 和 clientId**，能手动写进 `config.json`，或从安卓端导出后导入。|

如果你期待的是「装上去就能直接聊天」的东西，这里没有你要的。

---

## 它做什么 / 不做什么

- **只做协议转换**：在客户端和上游之间垫一层，把 uuid 写进请求体、补上 `X-Client-Id` 头、
  维护模型列表和流式响应。
- **不提供账号、不绕过付费、不批量注册**。uuid 是账户凭据，等同于密码，请自行保管。
- 上游本身就是标准 OpenAI 流式接口。支持自定义请求头的客户端（Kelivo、SillyTavern 等）
  可以不经过本工具直连，配置见 [windows/README.md](windows/README.md)。

| | [android/](android/) | [windows/](windows/) |
|---|---|---|
| 语言 | Kotlin / Jetpack Compose | Python（零第三方依赖） |
| 界面 | Material 3 | tkinter GUI / 终端 TUI(废弃) / 命令行 |
| 账户来源 | root 转储进程内存 | 手动填 uuid / 导入安卓导出的文件 |
| 本地代理 | 前台服务 | 独立进程 |
| 流式 SSE | 透传 / 本地合成 | 透传 / 本地合成 |
| 多账户 | UI 切换 | GUI / TUI(废弃)  切换 |
| 导入导出 | ✅ | ✅ |

---

## 快速开始

### Windows

需要事先准备好 uuid。两种方式：

1. **免环境 exe**：从 [Releases](https://github.com/Baidaofu/MiniTavernBridge/releases)
   取 `mtbridge-gui.exe`，和 `config.json` 放同一目录后双击。没有 Python 也能运行，
   无控制台窗口，界面跟随系统亮/暗主题。
2. **源码**：零第三方依赖，Python 3.11+ 即可。

```bash
cd windows
copy config.example.json config.json    # 填入自己的 uuid / clientId
python mtbridge.py --check              # 配置 / 网络 / 端口自检
python mtbridge_gui.py                  # 桌面面板
# 或 python mtbridge_tui.py             # 终端界面
# 或 python mtbridge.py                 # 纯代理
```

GUI 里可启停代理、切换/增删改账户、导入导出、刷新模型（显示配额倍率）、检测配额；
账户 UUID 和模型 ID/名称单击即复制。详见 [windows/README.md](windows/README.md)。

### Android

**需要 root。** 从 Releases 取 `app-debug.apk` 安装，或自行构建：

```bash
cd android
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

启动后在**账户页**点「扫描设备」提取已登录账户的 uuid，再到首页点「启动代理」。

<details>
<summary>arm64 / Termux 构建</summary>

Gradle 默认拉的 `aapt2` 是 x86_64 二进制，arm64 上会报 `Failed to start AAPT2 process`。
改用系统自带的 aapt2：

```properties
# ~/.gradle/gradle.properties
android.aapt2FromMavenOverride=/data/data/com.termux/files/usr/bin/aapt2
```
</details>

### 客户端配置（两端一致）

```
Base URL   http://127.0.0.1:8787/v1
API Key    任意字符串
Path       /chat/completions
```

端口可在 Windows 配置文件中修改，安卓端固定 8787。**同一台设备只能跑一个实例**，
两个都监听 8787 时后启动的会失败。

---

## 关于 uuid

MiniTavern 把本地数据全部加密（`apiSettings.json`、`userInfo.json` 是 AES，
`SecureStore.xml` 是 EncryptedSharedPreferences），AES 口令在 Android Keystore 里不可导出，
静态读文件走不通。

安卓端的做法是转储运行中进程的内存：解析 `/proc/<pid>/maps` 挑出 Hermes GC 堆，
导出后在 UTF-16 文本中匹配 JWT，解出 payload 里的 `uuid`。
Windows 端没有这一步，需要你自己提供。

**uuid 等同于账户密码**，导出文件里包含它，请按敏感数据对待。

---

## 模型与配额

模型目录来自上游的 `/api/api-keys/list`，需要带 `X-Client-Id`；
返回的 `title` 是内部短 id，`model` 是请求时必须使用的完整名。两端都做了自动转换，
并在列表里显示配额消耗倍率（字段 `consumeRate`）。

后端没有可用的配额查询接口，配额只能从任意一次对话响应的 `otherInfo` 里读取。
因此「检测配额」会真实发送一次极小的请求，消耗至少 1 点额度。

`GET /v1/models` 的 `id` 用完整模型名，短 id 放在 `mtb_id` 字段。

---

## 已知限制

- **流式是「能用但有条件」**：上游返回 SSE 时逐块透传；只给整包 JSON 时代理本地切成
  `chat.completion.chunk`。合成流的节奏与真实生成速度无关，chunk 大小固定 64 字符。
- **上下文窗口未知**，客户端需手填。
- **配额重置时间后端未提供**；配额按账户独立，到期后行为未知。
- **安卓版需要 root**，且 MiniTavern 必须处于登录状态。
- 部分模型（`gpt-5.4` / `gpt-6-astra`）的服务端 prompt 注入量与常规模型明显不同，
  不应视为对应官方模型。

---

## 构建

仓库自带 GitHub Actions：push 到 main 时并行构建 Android debug APK 与 Windows 单文件 exe，
产物保存在 Actions artifacts（30 天）。发布包由 Releases 提供。

```bash
# Android
cd android && ./gradlew assembleDebug

# Windows exe（需要 PyInstaller）
cd windows && python -m PyInstaller --onefile --windowed --name mtbridge-gui mtbridge_gui.py
```

---

## 合规提示

本工具会绕过 MiniTavern App 的设备签名校验，其服务端持续统计
`signature_ok` / `signature_fail` / `nonce_replay`。**这实质上违反其服务条款，
封号风险由使用者自行承担。**

仅供个人学习与研究。
