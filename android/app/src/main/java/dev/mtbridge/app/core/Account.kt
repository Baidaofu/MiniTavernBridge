package dev.mtbridge.app.core

import java.util.concurrent.CopyOnWriteArrayList

/**
 * One MiniTavern account.
 *
 * Only [uuid] plus [clientId] are actually required to talk to the backend —
 * the JWT is not validated by the server, and `clientId` is a constant derived
 * from the app itself. `token` is retained only because it carries the `sub`
 * (server-side user id) and a human-readable expiry.
 */
data class Account(
    val uuid: String,
    val clientId: String,
    val sub: String = "",
    val token: String = "",
    val tokenExp: Long = 0L,
    val label: String = "",
    val note: String = "",
    val createdAt: Long = System.currentTimeMillis(),
    val lastUsedAt: Long = 0L,
    val quotaTotal: Int = 0,
    val quotaUsed: Int = 0,
    val quotaUpdatedAt: Long = 0L,
) {
    val shortUuid: String get() = if (uuid.length > 12) uuid.take(12) + "…" else uuid
    val quotaLeft: Int get() = (quotaTotal - quotaUsed).coerceAtLeast(0)
    val displayName: String get() = label.ifBlank { "账户 ${uuid.take(6)}" }
}

/** Live usage snapshot reported by the backend in `otherInfo`. */
data class QuotaInfo(
    val total: Int,
    val used: Int,
    val internalModel: String = "",
) {
    val left: Int get() = (total - used).coerceAtLeast(0)
}

object Bus {
    val quotaUpdates = CopyOnWriteArrayList<(String, QuotaInfo) -> Unit>()
    val logs = CopyOnWriteArrayList<Pair<Long, String>>()

    fun log(msg: String) {
        logs.add(System.currentTimeMillis() to msg)
        if (logs.size > 500) logs.subList(0, 200).clear()
    }

    fun emitQuota(uuid: String, info: QuotaInfo) {
        quotaUpdates.forEach { it(uuid, info) }
    }
}
