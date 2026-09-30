package dev.mtbridge.app.core

import kotlinx.coroutines.flow.MutableStateFlow
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicLong

/**
 * 结构化日志。
 *
 * 之前只有 `Bus.log(String)` 的纯文本行，无法回答「刚才那次调用到底
 * 发了什么、回了什么」。现在改为带类型的条目 + 可展开的明细。
 */
sealed class LogEntry {
    abstract val id: Long
    abstract val time: Long
    abstract val level: Level
    abstract val title: String
    abstract val summary: String

    /** 非空表示这条记录有可展开的明细 */
    open val detail: String? = null

    /** 可复制到剪贴板的原文（请求/响应 JSON 等） */
    open val copyText: String? = null

    enum class Level { INFO, SUCCESS, WARN, ERROR, REQUEST }

    /** 纯文本事件：启动、切换、保存等 */
    data class Event(
        override val id: Long,
        override val time: Long,
        override val level: Level,
        override val title: String,
        override val summary: String,
        override val detail: String? = null,
    ) : LogEntry()

    /**
     * 一次上游调用的完整记录。
     * 点开后可查看归一化后的请求体、原始响应体与耗时/token。
     */
    data class Call(
        override val id: Long,
        override val time: Long,
        val model: String,
        val stream: Boolean,
        val accountLabel: String,
        val accountUuid: String,
        val latencyMs: Long,
        val status: Int?,
        val promptTokens: Int?,
        val completionTokens: Int?,
        val usedQuota: Int?,
        val totalQuota: Int?,
        val requestBody: String,
        val responseBody: String,
        val error: String?,
        val local: Boolean = false,
    ) : LogEntry() {
        override val level: Level get() = when {
            error != null -> Level.ERROR
            status != null && status !in 200..299 -> Level.WARN
            else -> Level.REQUEST
        }

        override val title: String
            get() = if (local) "本地请求 · ${modelShort(model)}"
            else "调用 ${modelShort(model)}"

        override val summary: String = buildString {
            append(if (stream) "流式" else "非流式")
            append(" · ").append(accountLabel)
            append(" · ").append(latencyMs).append(" ms")
            status?.let { append(" · HTTP ").append(it) }
            if (promptTokens != null || completionTokens != null) {
                append(" · token ").append(promptTokens ?: 0)
                append("/").append(completionTokens ?: 0)
            }
            if (usedQuota != null) append(" · 配额 ").append(usedQuota).append("/").append(totalQuota ?: 0)
            error?.let { append(" · ").append(it) }
        }

        override val detail: String
            get() = buildString {
                appendLine("── 请求体 ──────────────────────────────")
                appendLine(requestBody)
                appendLine()
                appendLine("── 响应 ────────────────────────────────")
                if (error != null) {
                    appendLine("错误: ").appendLine(error)
                }
                appendLine(responseBody)
            }

        override val copyText: String get() = requestBody + "\n\n" + responseBody
    }

    companion object {
        fun modelShort(m: String) = m.substringAfterLast('/').ifBlank { m }
    }
}

object LogBus {

    private val seq = AtomicLong(0)
    private fun nextId() = seq.incrementAndGet()

    private val _entries = MutableStateFlow<List<LogEntry>>(emptyList())
    val entries: kotlinx.coroutines.flow.StateFlow<List<LogEntry>> = _entries

    /** 纯文本事件，兼容旧调用点 */
    fun log(msg: String, level: LogEntry.Level = LogEntry.Level.INFO) {
        add(LogEntry.Event(nextId(), System.currentTimeMillis(), level, msg, ""))
    }

    fun add(e: LogEntry) {
        _entries.value = (_entries.value + e).takeLast(MAX)
    }

    fun event(
        title: String,
        summary: String = "",
        level: LogEntry.Level = LogEntry.Level.INFO,
        detail: String? = null,
    ) = add(LogEntry.Event(nextId(), System.currentTimeMillis(), level, title, summary, detail))

    fun call(
        model: String,
        stream: Boolean,
        accountLabel: String,
        accountUuid: String,
        latencyMs: Long,
        status: Int?,
        requestBody: String,
        responseBody: String,
        error: String? = null,
        local: Boolean = false,
    ) {
        val usage = runCatching { org.json.JSONObject(responseBody) }.getOrNull()
        val oi = usage?.optJSONObject("otherInfo")
        val us = usage?.optJSONObject("usage")
        add(
            LogEntry.Call(
                id = nextId(),
                time = System.currentTimeMillis(),
                model = model,
                stream = stream,
                accountLabel = accountLabel,
                accountUuid = accountUuid,
                latencyMs = latencyMs,
                status = status,
                promptTokens = us?.optInt("prompt_tokens")?.takeIf { us.has("prompt_tokens") },
                completionTokens = us?.optInt("completion_tokens")?.takeIf { us.has("completion_tokens") },
                usedQuota = oi?.optInt("usedQuota")?.takeIf { oi.has("usedQuota") },
                totalQuota = oi?.optInt("totalQuota")?.takeIf { oi.has("totalQuota") },
                requestBody = requestBody.take(MAX_TEXT),
                responseBody = responseBody.take(MAX_TEXT),
                error = error,
                local = local,
            )
        )
    }

    fun clear() {
        _entries.value = emptyList()
    }

    const val MAX = 300
    const val MAX_TEXT = 64_000
}

private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
private val dateFmt = SimpleDateFormat("MM-dd", Locale.getDefault())

fun Long.logTime(): String = timeFmt.format(java.util.Date(this))
fun Long.logDate(): String = dateFmt.format(java.util.Date(this))
fun Long.logDay(): String {
    val cal = java.util.Calendar.getInstance()
    val t = java.util.Calendar.getInstance().timeInMillis
    val days = ((t - this) / 86_400_000).toInt()
    return when (days) {
        0 -> "今天"; 1 -> "昨天"; in 2..6 -> "$days 天前"; else -> logDate()
    }
}
