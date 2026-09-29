package dev.mtbridge.app

import android.app.Application
import dev.mtbridge.app.core.AccountStore
import dev.mtbridge.app.core.Bus
import dev.mtbridge.app.core.MiniTavernApi
import dev.mtbridge.app.core.RootShell
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.ConcurrentHashMap

class MtApp : Application() {

    lateinit var store: AccountStore
        private set

    /** port, autoStartOnBoot */
    val settings = MutableStateFlow(Settings(port = 8787, autoStart = false))

    private val modelCache = ConcurrentHashMap<String, Pair<Long, List<MiniTavernApi.RemoteModel>>>()

    /** last known error per account, surfaced in the UI */
    val modelErrors = MutableStateFlow<Map<String, String>>(emptyMap())

    override fun onCreate() {
        super.onCreate()
        store = AccountStore(this)

        Bus.quotaUpdates.add { uuid, q -> store.updateQuota(uuid, q) }
        Bus.quotaUpdates.add { _, q -> Bus.log("配额更新 ${q.used}/${q.total}") }
    }

    fun cachedModels(uuid: String): Pair<String, List<MiniTavernApi.RemoteModel>> {
        val acc = store.accounts.value.firstOrNull { it.uuid == uuid }
            ?: return "账户不存在" to emptyList()
        val hit = modelCache[uuid]
        if (hit != null && System.currentTimeMillis() - hit.first < 5 * 60_000) {
            return "ok" to hit.second
        }
        return refreshModels(uuid)
    }

    /** Blocking network call; run off the main thread. */
    fun refreshModels(uuid: String): Pair<String, List<MiniTavernApi.RemoteModel>> {
        val acc = store.accounts.value.firstOrNull { it.uuid == uuid }
            ?: return "账户不存在" to emptyList()
        return MiniTavernApi.fetchModels(acc.clientId).fold(
            onSuccess = { list ->
                modelCache[uuid] = System.currentTimeMillis() to list
                modelErrors.value = modelErrors.value - uuid
                Bus.log("已获取 ${list.size} 个模型")
                "ok" to list
            },
            onFailure = { e ->
                val msg = e.message ?: "未知错误"
                modelErrors.value = modelErrors.value + (uuid to msg)
                Bus.log("模型列表获取失败: $msg")
                msg to emptyList()
            },
        )
    }

    fun rootAvailable(): Boolean = RootShell.isAvailable()

    data class Settings(val port: Int = 8787, val autoStart: Boolean = false)
}
