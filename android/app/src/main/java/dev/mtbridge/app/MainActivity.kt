package dev.mtbridge.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.padding
import dev.mtbridge.app.ui.icons.Home
import dev.mtbridge.app.ui.icons.Person
import dev.mtbridge.app.ui.icons.Terminal
import androidx.compose.material3.Icon
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat
import dev.mtbridge.app.core.Account
import dev.mtbridge.app.core.Bus
import dev.mtbridge.app.core.LogBus
import dev.mtbridge.app.core.LogEntry
import dev.mtbridge.app.core.Constants
import dev.mtbridge.app.core.Extractor
import dev.mtbridge.app.core.MiniTavernApi
import dev.mtbridge.app.core.QuotaInfo
import dev.mtbridge.app.core.RootShell
import dev.mtbridge.app.core.TestAccount
import dev.mtbridge.app.proxy.ProxyService
import dev.mtbridge.app.ui.AccountsScreen
import dev.mtbridge.app.ui.HomeScreen
import dev.mtbridge.app.ui.LogScreen
import dev.mtbridge.app.ui.MiniTavernBridgeTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class MainActivity : ComponentActivity() {

    private val app by lazy { application as MtApp }

    private val exportLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("application/json")
    ) { uri -> if (uri != null) writeExport(uri) }

    private val importLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri -> if (uri != null) readImport(uri) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        setContent {
            MiniTavernBridgeTheme {
                Root(
                    app = app,
                    onExport = { exportLauncher.launch("minitavern-accounts.json") },
                    onImport = { importLauncher.launch(arrayOf("application/json", "text/plain", "*/*")) },
                )
            }
        }
    }

    private fun writeExport(uri: Uri) {
        runCatching {
            contentResolver.openOutputStream(uri)?.use {
                it.write(app.store.exportJson().toByteArray())
            }
        }.onSuccess { LogBus.event("导出账户列表", "已写入 $uri", LogEntry.Level.SUCCESS) }
    }

    private fun readImport(uri: Uri) {
        runCatching {
            val text = contentResolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() }
                ?: return
            val (added, skipped) = app.store.importJson(text, replace = false)
            LogBus.event("导入账户列表", "新增 $added，跳过重复 $skipped", LogEntry.Level.SUCCESS)
        }.onFailure { LogBus.event("导入失败", it.message ?: "", LogEntry.Level.ERROR) }
    }

    @Composable
    private fun Root(app: MtApp, onExport: () -> Unit, onImport: () -> Unit) {
        val scope = rememberCoroutineScope()
        val snackbar = remember { SnackbarHostState() }

        var tab by remember { mutableStateOf(0) }
        var rootOk by remember { mutableStateOf(false) }
        var proxyRunning by remember { mutableStateOf(false) }
        var scanning by remember { mutableStateOf(false) }
        var probing by remember { mutableStateOf(false) }
        var models by remember { mutableStateOf<List<MiniTavernApi.RemoteModel>>(emptyList()) }
        var modelError by remember { mutableStateOf<String?>(null) }
        var editing by remember { mutableStateOf<Account?>(null) }
        // 调试入口需在账户页连点标题 5 次解锁；上抛到这里以跨页签保持
        var debugUnlocked by remember { mutableStateOf(false) }

        val accounts by app.store.accounts.collectAsState()
        val activeUuid by app.store.activeUuid.collectAsState()
        val account = accounts.firstOrNull { it.uuid == activeUuid }
        val logs by LogBus.entries.collectAsState()

        LaunchedEffect(Unit) {
            rootOk = withContext(Dispatchers.IO) { RootShell.requestAndCheck() }
            LogBus.event(
                "应用启动",
                "root ${if (rootOk) "已获取" else "未获取"} · 账户 ${app.store.accounts.value.size} 个",
                LogEntry.Level.SUCCESS,
            )
        }

        // reload models whenever the active account changes
        LaunchedEffect(activeUuid) {
            models = emptyList(); modelError = null
            val acc = account ?: return@LaunchedEffect
            val (err, list) = withContext(Dispatchers.IO) { app.refreshModels(acc.uuid) }
            models = list
            modelError = if (err == "ok") null else err
        }

        fun scanNow() {
            scanning = true
            scope.launch {
                val result = withContext(Dispatchers.IO) { Extractor().scan() }
                scanning = false
                if (result.accounts.isEmpty()) {
                    snackbar.showSnackbar(result.note)
                } else {
                    var added = 0
                    result.accounts.forEach { if (app.store.upsert(it)) added++ }
                    snackbar.showSnackbar("${result.note}，新增 $added 个")
                }
            }
        }

        fun refreshQuota() {
            val acc = account ?: return
            probing = true
            scope.launch {
                val probeModel = models.firstOrNull()?.name ?: "deepseek/deepseek-v3.2-exp"
                val r = withContext(Dispatchers.IO) {
                    MiniTavernApi.probeQuota(acc.uuid, acc.clientId, probeModel)
                }
                probing = false
                r.onSuccess {
                    app.store.updateQuota(acc.uuid, it)
                    snackbar.showSnackbar("配额 ${it.used}/${it.total}（本次检测消耗 1 点）")
                }.onFailure {
                    snackbar.showSnackbar("配额获取失败: ${it.message}")
                }
            }
        }


        Scaffold(
            // 窗口是 edge-to-edge（导航栏透明），Scaffold 默认会消费系统底部
            // inset 并把它算成内容区的 padding，而 NavigationBar 默认又不
            // 把手势区画上自己的背景 —— 中间就空出一条纯黑。
            // 这里把 contentWindowInsets 清零，改由 NavigationBar 自己吃
            // navigationBars inset 并绘制到底。
            contentWindowInsets = WindowInsets(0, 0, 0, 0),
            containerColor = MaterialTheme.colorScheme.surface,
            bottomBar = {
                NavigationBar(
                    containerColor = MaterialTheme.colorScheme.surfaceContainer,
                    tonalElevation = 0.dp,
                    windowInsets = WindowInsets.navigationBars,
                ) {
                    listOf(
                        Triple("首页", Home, Home),
                        Triple("账户", Person, Person),
                        Triple("日志", Terminal, Terminal),
                    ).forEachIndexed { i, item ->
                        NavigationBarItem(
                            selected = tab == i,
                            onClick = { tab = i },
                            icon = { Icon(item.second, item.first) },
                            label = { Text(item.first) },
                            colors = NavigationBarItemDefaults.colors(
                                indicatorColor = MaterialTheme.colorScheme.secondaryContainer,
                            ),
                        )
                    }
                }
            }
        ) { pad ->
            Box(
                Modifier
                    .padding(pad)
                    .consumeWindowInsets(pad)
            ) {
                when (tab) {
                    0 -> HomeScreen(
                        rootOk = rootOk,
                        proxyRunning = proxyRunning,
                        port = app.settings.value.port,
                        account = account,
                        accounts = accounts,
                        models = models,
                        modelError = modelError,
                        probing = probing,
                        onStartProxy = {
                            ProxyService.start(this@MainActivity, app.settings.value.port)
                            proxyRunning = true
                        },
                        onStopProxy = {
                            ProxyService.stop(this@MainActivity)
                            proxyRunning = false
                        },
                        onRefreshQuota = { refreshQuota() },
                        onRefreshModels = {
                            val acc = account ?: return@HomeScreen
                            scope.launch {
                                val (err, list) = withContext(Dispatchers.IO) { app.refreshModels(acc.uuid) }
                                models = list
                                modelError = if (err == "ok") null else err
                            }
                        },
                        snackbar = snackbar,
                    )

                    1 -> AccountsScreen(
                        accounts = accounts,
                        activeUuid = activeUuid,
                        onSelect = { app.store.setActive(it) },
                        onEdit = { editing = it },
                        onDelete = { app.store.remove(it.uuid) },
                        onAddManual = { uuid, cid ->
                            if (uuid.length >= 16) {
                                app.store.upsert(
                                    Account(uuid = uuid, clientId = cid, label = "手动添加")
                                )
                            }
                        },
                        onImport = onImport,
                        onExport = onExport,
                        rootOk = rootOk,
                        scanning = scanning,
                        onScan = { scanNow() },
                        debugUnlocked = debugUnlocked,
                        onDebugUnlocked = { debugUnlocked = true },
                        onNewTestAccount = {
                            scope.launch {
                                snackbar.showSnackbar("正在开户…")
                                val r = withContext(Dispatchers.IO) {
                                    TestAccount.provision(Constants.CLIENT_ID_FALLBACK)
                                }
                                r.onSuccess { acc ->
                                    app.store.upsert(acc)
                                    app.store.setActive(acc.uuid)
                                    snackbar.showSnackbar(
                                        "已创建 ${acc.shortUuid}，配额 ${acc.quotaUsed}/${acc.quotaTotal}"
                                    )
                                }.onFailure {
                                    snackbar.showSnackbar("开户失败: ${it.message}")
                                }
                            }
                        },
                        snackbar = snackbar,
                    )

                    2 -> LogScreen(logs)
                }
            }
        }

        // export lives on the accounts tab header via long-press shortcut;
        // simplest reliable trigger is the FAB row on Home.
        editing?.let { target ->
            dev.mtbridge.app.ui.EditAccountDialog(
                account = target,
                onDismiss = { editing = null },
                onSave = { label, note ->
                    app.store.rename(target.uuid, label, note)
                    editing = null
                },
            )
        }
    }

    private fun fmtLog(pair: Pair<Long, String>): String {
        val t = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
            .format(java.util.Date(pair.first))
        return "$t  ${pair.second}"
    }
}
