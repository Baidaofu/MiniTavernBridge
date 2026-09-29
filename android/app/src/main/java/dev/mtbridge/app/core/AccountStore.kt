package dev.mtbridge.app.core

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Account persistence + selection.
 *
 * Stored as a plain JSON array in the app's private dir. Writes are atomic
 * (tmp + rename) so a crash mid-write cannot truncate the store.
 */
class AccountStore(private val ctx: Context) {

    private val file = File(ctx.filesDir, "accounts.json")
    private val _accounts = MutableStateFlow<List<Account>>(emptyList())
    val accounts: StateFlow<List<Account>> = _accounts.asStateFlow()

    private val _activeUuid = MutableStateFlow<String?>(null)
    val activeUuid: StateFlow<String?> = _activeUuid.asStateFlow()

    val active: Account? get() = _accounts.value.firstOrNull { it.uuid == _activeUuid.value }

    init { load() }

    private fun load() {
        if (!file.exists()) return
        val list = runCatching {
            val arr = JSONArray(file.readText())
            (0 until arr.length()).mapNotNull { i ->
                arr.optJSONObject(i)?.toAccount()
            }
        }.getOrDefault(emptyList())
        _accounts.value = list
        _activeUuid.value = list.firstOrNull()?.uuid
        Bus.log("已加载 ${list.size} 个账户")
    }

    private fun persist() {
        val arr = JSONArray()
        _accounts.value.forEach { a ->
            arr.put(JSONObject().apply {
                put("uuid", a.uuid)
                put("clientId", a.clientId)
                put("sub", a.sub)
                put("token", a.token)
                put("tokenExp", a.tokenExp)
                put("label", a.label)
                put("note", a.note)
                put("createdAt", a.createdAt)
                put("lastUsedAt", a.lastUsedAt)
                put("quotaTotal", a.quotaTotal)
                put("quotaUsed", a.quotaUsed)
                put("quotaUpdatedAt", a.quotaUpdatedAt)
            })
        }
        runCatching {
            val tmp = File(ctx.filesDir, "accounts.json.tmp")
            tmp.writeText(arr.toString())
            tmp.renameTo(file)
        }.onFailure { Bus.log("保存失败: ${it.message}") }
    }

    private fun JSONObject.toAccount() = Account(
        uuid = optString("uuid"),
        clientId = optString("clientId"),
        sub = optString("sub"),
        token = optString("token"),
        tokenExp = optLong("tokenExp"),
        label = optString("label"),
        note = optString("note"),
        createdAt = optLong("createdAt", System.currentTimeMillis()),
        lastUsedAt = optLong("lastUsedAt"),
        quotaTotal = optInt("quotaTotal"),
        quotaUsed = optInt("quotaUsed"),
        quotaUpdatedAt = optLong("quotaUpdatedAt"),
    )

    fun upsert(acc: Account): Boolean {
        val cur = _accounts.value
        // Same uuid = same account, refresh volatile fields but keep user data.
        val idx = cur.indexOfFirst { it.uuid == acc.uuid }
        val merged = if (idx >= 0) {
            val old = cur[idx]
            acc.copy(
                label = if (acc.label.isBlank()) old.label else acc.label,
                note = if (acc.note.isBlank()) old.note else old.note,
                createdAt = old.createdAt,
                quotaTotal = if (acc.quotaTotal > 0) acc.quotaTotal else old.quotaTotal,
                quotaUsed = if (acc.quotaUpdatedAt > 0) acc.quotaUsed else old.quotaUsed,
                quotaUpdatedAt = if (acc.quotaUpdatedAt > 0) acc.quotaUpdatedAt else old.quotaUpdatedAt,
            ).also { _accounts.value = cur.toMutableList().also { l -> l[idx] = it } }
        } else {
            _accounts.value = cur + acc
        }
        if (_activeUuid.value == null) _activeUuid.value = acc.uuid
        persist()
        return idx < 0
    }

    fun rename(uuid: String, label: String, note: String) {
        _accounts.value = _accounts.value.map {
            if (it.uuid == uuid) it.copy(label = label, note = note) else it
        }
        persist()
    }

    fun remove(uuid: String) {
        _accounts.value = _accounts.value.filterNot { it.uuid == uuid }
        if (_activeUuid.value == uuid) _activeUuid.value = _accounts.value.firstOrNull()?.uuid
        persist()
    }

    fun setActive(uuid: String) {
        if (_accounts.value.any { it.uuid == uuid }) {
            _activeUuid.value = uuid
            Bus.log("切换到账户 ${uuid.take(8)}")
        }
    }

    fun touchUsed(uuid: String) {
        _accounts.value = _accounts.value.map {
            if (it.uuid == uuid) it.copy(lastUsedAt = System.currentTimeMillis()) else it
        }
        persist()
    }

    fun updateQuota(uuid: String, q: QuotaInfo) {
        _accounts.value = _accounts.value.map {
            if (it.uuid == uuid) it.copy(
                quotaTotal = q.total,
                quotaUsed = q.used,
                quotaUpdatedAt = System.currentTimeMillis(),
            ) else it
        }
        persist()
    }

    // ------------------------------------------------------------ export/import

    fun exportJson(): String {
        val arr = JSONArray()
        _accounts.value.forEach { a ->
            arr.put(JSONObject().apply {
                put("uuid", a.uuid)
                put("clientId", a.clientId)
                put("sub", a.sub)
                put("label", a.label)
                put("note", a.note)
                put("token", a.token)
                put("tokenExp", a.tokenExp)
                put("quotaTotal", a.quotaTotal)
                put("quotaUsed", a.quotaUsed)
            })
        }
        return JSONObject().apply {
            put("format", "mtbridge-accounts")
            put("version", 1)
            put("exportedAt", System.currentTimeMillis())
            put("count", arr.length())
            put("accounts", arr)
        }.toString(2)
    }

    /** Returns (imported, skipped) counts. Duplicates by uuid are skipped. */
    fun importJson(raw: String, replace: Boolean): Pair<Int, Int> {
        val root = JSONObject(raw)
        val arr = root.optJSONArray("accounts") ?: JSONArray(raw)
        if (replace) {
            _accounts.value = emptyList()
        }
        var added = 0
        var skipped = 0
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val acc = o.toAccount()
            if (acc.uuid.isBlank()) continue
            if (_accounts.value.any { it.uuid == acc.uuid }) { skipped++; continue }
            _accounts.value = _accounts.value + acc
            added++
        }
        if (_activeUuid.value == null) _activeUuid.value = _accounts.value.firstOrNull()?.uuid
        persist()
        return added to skipped
    }
}
