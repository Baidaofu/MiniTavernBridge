package dev.mtbridge.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import dev.mtbridge.app.ui.icons.PlayArrow
import dev.mtbridge.app.ui.icons.Refresh
import dev.mtbridge.app.ui.icons.Radar
import dev.mtbridge.app.ui.icons.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.mtbridge.app.core.Account
import dev.mtbridge.app.core.MiniTavernApi

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    rootOk: Boolean,
    proxyRunning: Boolean,
    port: Int,
    account: Account?,
    accounts: List<Account>,
    models: List<MiniTavernApi.RemoteModel>,
    modelError: String?,
    scanning: Boolean,
    onScan: () -> Unit,
    onStartProxy: () -> Unit,
    onStopProxy: () -> Unit,
    onRefreshQuota: () -> Unit,
    onRefreshModels: () -> Unit,
    snackbar: SnackbarHostState,
) {
    var showPort by remember { mutableStateOf(false) }

    Scaffold(
        topBar = { TopAppBar(title = { Text("MiniTavern Bridge") }) },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(pad).padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {

            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("运行状态", style = MaterialTheme.typography.titleMedium)
                        StatusRow("root 权限", if (rootOk) "已获取" else "未获取", rootOk)
                        StatusRow(
                            "本地代理",
                            if (proxyRunning) "运行中  127.0.0.1:$port" else "已停止",
                            proxyRunning,
                        )
                        StatusRow("活动账户", account?.displayName ?: "未选择", account != null)
                        StatusRow("模型列表", if (models.isEmpty()) (modelError ?: "未加载") else "${models.size} 个", models.isNotEmpty())

                        Spacer(Modifier.height(2.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (proxyRunning) {
                                FilledTonalButton(onClick = onStopProxy) {
                                    Icon(Stop, null); Text(" 停止代理")
                                }
                            } else {
                                Button(
                                    onClick = onStartProxy,
                                    enabled = account != null,
                                ) {
                                    Icon(PlayArrow, null); Text(" 启动代理")
                                }
                            }
                            OutlinedButton(onClick = { showPort = true }) { Text("端口") }
                        }
                    }
                }
            }

            item { QuotaCard(account) }

            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("提取账户", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "需要 root。App 会从 MiniTavern 进程内存中读取已解密的会话信息。" +
                                "请确保 MiniTavern 已安装并处于登录状态。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Button(onClick = onScan, enabled = rootOk && !scanning) {
                            if (scanning) {
                                CircularProgressIndicator(
                                    Modifier.size(18.dp),
                                    strokeWidth = 2.dp,
                                    color = MaterialTheme.colorScheme.onPrimary,
                                )
                                Text(" 扫描中…")
                            } else {
                                Icon(Radar, null); Text(" 扫描设备")
                            }
                        }
                    }
                }
            }

            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text("可用模型 (${models.size})", style = MaterialTheme.typography.titleMedium)
                            IconButton(onClick = onRefreshModels, enabled = account != null) {
                                Icon(Refresh, "刷新")
                            }
                        }
                        if (models.isEmpty()) {
                            Text(
                                modelError ?: "选择一个账户后点右上角刷新",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        } else {
                            // 短 id → 完整模型名的映射表
                            models.forEach { m ->
                                Row(
                                    Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 3.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Text(
                                        m.id,
                                        style = MaterialTheme.typography.labelMedium,
                                        fontFamily = FontFamily.Monospace,
                                        color = MaterialTheme.colorScheme.primary,
                                        modifier = Modifier.width(132.dp),
                                    )
                                    Text(
                                        "→",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.outline,
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    Text(
                                        m.name,
                                        style = MaterialTheme.typography.bodySmall,
                                        fontFamily = FontFamily.Monospace,
                                    )
                                }
                            }
                        }
                        OutlinedButton(onClick = onRefreshQuota, enabled = account != null) {
                            Text("刷新配额")
                        }
                    }
                }
            }

            if (accounts.size > 1) {
                item {
                    Text("快速切换", style = MaterialTheme.typography.titleSmall)
                }
            }

            item { Spacer(Modifier.height(80.dp)) }
        }
    }

    if (showPort) {
        AlertDialog(
            onDismissRequest = { showPort = false },
            title = { Text("本地地址") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("在其他客户端中填写：", style = MaterialTheme.typography.bodySmall)
                    Text(
                        "Base URL  http://127.0.0.1:$port/v1\n" +
                            "API Key   任意字符串\n" +
                            "Path      /chat/completions",
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                    )
                    Text(
                        "后端需要 uuid 位于请求体中，pi / Kelivo 等无法设置 body 的客户端" +
                            "必须经由本代理转发。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            },
            confirmButton = { TextButton(onClick = { showPort = false }) { Text("好") } },
        )
    }
}

@Composable
private fun StatusRow(label: String, value: String, good: Boolean) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            color = if (good) MaterialTheme.colorScheme.primary
            else MaterialTheme.colorScheme.error,
            fontFamily = FontFamily.Monospace,
        )
    }
}
