package dev.mtbridge.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import dev.mtbridge.app.ui.icons.Add
import dev.mtbridge.app.ui.icons.BugReport
import dev.mtbridge.app.ui.icons.Delete
import dev.mtbridge.app.ui.icons.Download
import dev.mtbridge.app.ui.icons.Edit
import dev.mtbridge.app.ui.icons.RadioButtonChecked
import dev.mtbridge.app.ui.icons.RadioButtonUnchecked
import dev.mtbridge.app.ui.icons.Radar
import dev.mtbridge.app.ui.icons.Upload
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.mtbridge.app.core.Account
import kotlinx.coroutines.launch

private const val UNLOCK_TAPS = 5

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AccountsScreen(
    accounts: List<Account>,
    activeUuid: String?,
    onSelect: (String) -> Unit,
    onEdit: (Account) -> Unit,
    onDelete: (Account) -> Unit,
    onAddManual: (String, String) -> Unit,
    onImport: () -> Unit,
    onExport: () -> Unit,
    rootOk: Boolean,
    scanning: Boolean,
    onScan: () -> Unit,
    debugUnlocked: Boolean,
    onDebugUnlocked: () -> Unit,
    onNewTestAccount: () -> Unit,
    snackbar: SnackbarHostState,
) {
    var pendingDelete by remember { mutableStateOf<Account?>(null) }
    var showAdd by remember { mutableStateOf(false) }
    // 调试入口默认隐藏：连点标题 5 次才现身。
    // 解锁状态上抛到调用方，切页签不会重新隐藏。
    var titleTaps by remember { mutableStateOf(0) }
    val scope = rememberCoroutineScope()

    Scaffold(
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                windowInsets = WindowInsets.statusBars,
                title = {
                    Text(
                        "账户 (${accounts.size})",
                        modifier = Modifier
                            .clickable {
                                titleTaps++
                                if (titleTaps >= UNLOCK_TAPS) {
                                    titleTaps = 0
                                    onDebugUnlocked()
                                    scope.launch { snackbar.showSnackbar("调试功能已解锁") }
                                }
                            }
                            .padding(vertical = 10.dp),
                    )
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { pad ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(pad).padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item {
                Card(
                    Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(28.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.secondaryContainer),
                ) {
                    Column(Modifier.padding(20.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("提取账户", style = MaterialTheme.typography.titleMedium,
                            color = MaterialTheme.colorScheme.onSecondaryContainer)
                        Text(
                            "需要 root。从 MiniTavern 进程内存读取已解密的会话信息，" +
                                "请确保它已安装并处于登录状态。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSecondaryContainer,
                        )
                        Button(
                            onClick = onScan,
                            enabled = rootOk && !scanning,
                            shape = RoundedCornerShape(20.dp),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            if (scanning) {
                                CircularProgressIndicator(
                                    Modifier.size(16.dp), strokeWidth = 2.dp,
                                    color = MaterialTheme.colorScheme.onPrimary)
                                Text(" 扫描中…")
                            } else {
                                Icon(Radar, null, Modifier.size(18.dp)); Text(" 扫描设备")
                            }
                        }
                    }
                }
            }

            item {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Button(
                        onClick = { showAdd = true },
                        modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(horizontal = 4.dp),
                    ) {
                        Icon(Add, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("添加", maxLines = 1, softWrap = false)
                    }
                    OutlinedButton(
                        onClick = onImport,
                        modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(horizontal = 4.dp),
                    ) {
                        // 数据流入应用 = 下载箭头
                        Icon(Download, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("导入", maxLines = 1, softWrap = false)
                    }
                    OutlinedButton(
                        onClick = onExport,
                        modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(horizontal = 4.dp),
                    ) {
                        // 数据流出应用 = 上传箭头
                        Icon(Upload, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("导出", maxLines = 1, softWrap = false)
                    }
                }
            }

            if (debugUnlocked) {
                item {
                    // 调试入口：后端对新 uuid 会自动开户并发放免费配额，
                    // 手边没有多余真机账户时用它验证链路。仅本地调试用。
                    Row(
                        Modifier.fillMaxWidth().padding(top = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        OutlinedButton(
                            onClick = onNewTestAccount,
                            contentPadding = PaddingValues(horizontal = 12.dp),
                        ) {
                            Icon(BugReport, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("调试账户", maxLines = 1, softWrap = false)
                        }
                        Text(
                            "后端会给新 uuid 自动开户并发放免费配额",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            if (accounts.isEmpty()) {
                item {
                    Card(Modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("还没有账户", style = MaterialTheme.typography.titleMedium)
                            Text(
                                "回到「首页」点「扫描设备」自动提取，或手动填写 uuid。",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }

            items(accounts, key = { it.uuid }) { acc ->
                val isActive = acc.uuid == activeUuid
                Card(Modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large) {
                    Row(
                        Modifier.fillMaxWidth().padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        IconButton(onClick = { onSelect(acc.uuid) }) {
                            Icon(
                                if (isActive) RadioButtonChecked
                                else RadioButtonUnchecked,
                                contentDescription = "选择",
                                tint = if (isActive) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.outline,
                            )
                        }
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(
                                acc.displayName,
                                style = MaterialTheme.typography.titleSmall,
                            )
                            Text(
                                acc.uuid,
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Text(
                                "剩余 ${acc.quotaLeft}/${acc.quotaTotal} · " +
                                    "扫描 ${acc.createdAt.fmtDateTime()}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        IconButton(onClick = { onEdit(acc) }) {
                            Icon(Edit, "编辑")
                        }
                        IconButton(onClick = { pendingDelete = acc }) {
                            Icon(
                                Delete, "删除",
                                tint = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                }
            }

            item { Spacer(Modifier.height(80.dp)) }
        }
    }

    pendingDelete?.let { target ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text("删除账户？") },
            text = { Text("将从列表中移除 ${target.displayName}。此操作不会影响 MiniTavern App 本身。") },
            confirmButton = {
                TextButton(onClick = { onDelete(target); pendingDelete = null }) {
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = { TextButton(onClick = { pendingDelete = null }) { Text("取消") } },
        )
    }

    if (showAdd) {
        AddAccountDialog(
            onDismiss = { showAdd = false },
            onConfirm = { uuid, clientId ->
                onAddManual(uuid, clientId)
                showAdd = false
            },
        )
    }
}

@Composable
fun EditAccountDialog(
    account: Account,
    onDismiss: () -> Unit,
    onSave: (String, String) -> Unit,
) {
    var label by remember { mutableStateOf(account.label) }
    var note by remember { mutableStateOf(account.note) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("编辑账户") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = label,
                    onValueChange = { label = it },
                    label = { Text("备注名") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = note,
                    onValueChange = { note = it },
                    label = { Text("备注") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "uuid  ${account.uuid}",
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                )
            }
        },
        confirmButton = { TextButton(onClick = { onSave(label, note) }) { Text("保存") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun AddAccountDialog(
    onDismiss: () -> Unit,
    onConfirm: (String, String) -> Unit,
) {
    var uuid by remember { mutableStateOf("") }
    var cid by remember { mutableStateOf("68cd199d61947054fdf25ebe") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("手动添加账户") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    "需要该账户的 uuid。可用首页的「扫描设备」自动获取。",
                    style = MaterialTheme.typography.bodySmall,
                )
                OutlinedTextField(
                    value = uuid,
                    onValueChange = { uuid = it.trim() },
                    label = { Text("uuid") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = cid,
                    onValueChange = { cid = it.trim() },
                    label = { Text("clientId") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(uuid, cid.ifBlank { "68cd199d61947054fdf25ebe" }) },
                enabled = uuid.length >= 16,
            ) { Text("添加") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
