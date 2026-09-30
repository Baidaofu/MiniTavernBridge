# Windows 终端界面（TUI）

`mtbridge_tui.py` 是 `mtbridge.py` 的带界面外壳，零第三方依赖
（纯标准库 + ANSI 转义序列）。

```bash
python mtbridge_tui.py            # 前台运行代理 + TUI
python mtbridge_tui.py --headless # 只跑代理，不开界面
```

非交互终端（管道、重定向、CI）下会自动降级为 headless。

---

## 界面

```
  MiniTavern Bridge TUI   v1.0.0   ● 运行中   http://127.0.0.1:8787/v1

  活动账户  手机A   配额 12/100   账户 2   模型 24   请求 37

  最近调用
    02:14:31  deepseek-v3.2          1720 ms  HTTP 200
    02:14:02  gemini-3.1-pro        11800 ms  HTTP 200

  账户   ↑↓ 选择 · Enter 切换 · e 停用/启用 · d 删除 · r 重命名
    ▶ 手机A       a1b2c3d4…   12/100
      手机B       e5f6a7b8…   [已停用]
```

顶部是服务状态与配额，中间是最近调用，底部是可操作的账户表。

---

## 键位

| 键 | 作用 |
|---|---|
| `↑` `↓` / `k` `j` | 选择账户 |
| `Enter` / `Space` | 切换到选中账户（写回 config.json） |
| `e` | 停用 / 启用账户（停用的不参与选择） |
| `d` | 删除账户 |
| `r` | 提示去 config.json 改 label |
| `R` | 立即刷新模型列表与配额 |
| `q` | 退出 |

后台每 20 秒自动刷新一次模型数与各账户配额。

---

## 导入导出

TUI 里不做文件浏览器（终端下体验差），用命令行参数：

```bash
# 导出
python mtbridge_tui.py --export accounts.json

# 导入（按 uuid 去重）
python mtbridge_tui.py --import_ accounts.json

# 导入并替换现有全部账户
python mtbridge_tui.py --import_ accounts.json --replace
```

导出格式：

```json
{
  "format": "mtbridge-accounts",
  "version": 1,
  "count": 2,
  "accounts": [
    {
      "label": "手机A",
      "uuid": "…",
      "clientId": "…",
      "note": "",
      "enabled": true
    }
  ]
}
```

与安卓版 `AccountStore.exportJson()` 同名同结构，**两边的账户列表可以互换**。
`uuid` 长度不足 16 位或已存在的会被跳过并计入 skipped。

---

## 端口

```bash
python mtbridge_tui.py --port 8899
```

覆盖 `config.json` 里的 `listen.port`。

---

## 与 mtbridge.py 的关系

`mtbridge_tui.py` 只是外壳，代理逻辑全部复用 `mtbridge.py` 的
`Handler` / `Upstream` / `State`。两者可以同时跑在不同端口，不会冲突。

`--headless` 模式和 `python mtbridge.py` 等价。
