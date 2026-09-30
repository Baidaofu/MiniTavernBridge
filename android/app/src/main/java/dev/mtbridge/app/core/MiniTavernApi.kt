package dev.mtbridge.app.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * Talks to the MiniTavern backend on behalf of the local proxy.
 *
 * Auth model, established by probing the live service:
 *   - `X-Client-Id` header is required, otherwise "用户不存在"
 *   - `uuid` must appear in the JSON body, otherwise "uuid should not be empty"
 *   - `Authorization: Bearer <JWT>` is ignored entirely (valid/garbage/absent
 *     all behave identically), so it is deliberately not sent
 *   - `model` must be the full name; internal short ids are rejected
 */
object MiniTavernApi {

    data class RemoteModel(val id: String, val name: String, val description: String)

    private fun open(path: String, method: String, body: String?): Pair<Int, String> {
        val conn = URL(Constants.UPSTREAM + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 15_000
        conn.readTimeout = 300_000
        conn.setRequestProperty("X-Client-Id", currentClientId)
        conn.setRequestProperty("Content-Type", "application/json")
        if (body != null) {
            conn.doOutput = true
            conn.outputStream.use { it.write(body.toByteArray()) }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.let { BufferedReader(InputStreamReader(it)).use(BufferedReader::readText) } ?: ""
        conn.disconnect()
        return code to text
    }

    /** Mutable so the proxy can follow the active account. */
    @Volatile var currentClientId: String = Constants.CLIENT_ID_FALLBACK

    /** GET /api/api-keys/list — this is the real, authenticated model catalog. */
    fun fetchModels(clientId: String): Result<List<RemoteModel>> = runCatching {
        val saved = currentClientId
        currentClientId = clientId
        try {
            val (code, text) = open("/api/api-keys/list", "GET", null)
            if (code !in 200..299) error("HTTP $code: ${text.take(200)}")
            val arr = JSONArray(text)
            (0 until arr.length()).mapNotNull { i ->
                val o = arr.optJSONObject(i) ?: return@mapNotNull null
                RemoteModel(
                    id = o.optString("title").ifBlank { o.optString("model") },
                    name = o.optString("model").ifBlank { o.optString("name") },
                    description = o.optString("description"),
                )
            }
        } finally {
            currentClientId = saved
        }
    }

    /** Upstream answered with a non-2xx status; [code] is what it said. */
    class UpstreamHttpError(val code: Int, message: String) : Exception(message)

    /**
     * An opened upstream chat call: the body is NOT consumed yet, so the caller
     * can relay an SSE stream chunk by chunk. Always [close] it.
     */
    class ChatUpstream(
        private val conn: HttpURLConnection,
        val contentType: String,
    ) : java.io.Closeable {
        val body: java.io.InputStream get() = conn.inputStream
        val isEventStream: Boolean
            get() = contentType.substringBefore(';').trim()
                .equals("text/event-stream", ignoreCase = true)
        override fun close() {
            runCatching { conn.inputStream.close() }
            conn.disconnect()
        }
    }

    /**
     * Opens a chat completion call and hands back the live response stream.
     * Non-2xx becomes a failed [Result] whose message carries the upstream body.
     */
    fun openChat(uuid: String, clientId: String, payload: JSONObject): Result<ChatUpstream> =
        runCatching {
            val obj = JSONObject(payload.toString())
            obj.put("uuid", uuid)
            val stream = obj.optBoolean("stream", false)
            val conn = URL(Constants.UPSTREAM + "/api/ai-proxy/chat/completions")
                .openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 15_000
            conn.readTimeout = 300_000
            conn.setRequestProperty("X-Client-Id", clientId)
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty(
                "Accept",
                if (stream) "text/event-stream, application/json" else "application/json",
            )
            conn.doOutput = true
            conn.outputStream.use { it.write(obj.toString().toByteArray()) }
            val code = conn.responseCode
            if (code !in 200..299) {
                val err = conn.errorStream
                    ?.let { BufferedReader(InputStreamReader(it)).use(BufferedReader::readText) }
                    .orEmpty()
                conn.disconnect()
                throw UpstreamHttpError(code, "HTTP $code: ${err.take(300)}")
            }
            ChatUpstream(conn, conn.contentType.orEmpty())
        }

    /**
     * Pulls `otherInfo` quota counters out of a complete response body — either
     * a JSON document or a batch of `data:` SSE events.
     */
    fun quotaOf(body: String): QuotaInfo? {
        quotaIn(body)?.let { return it }
        for (line in body.lineSequence()) {
            val t = line.trim()
            if (!t.startsWith("data:")) continue
            val payload = t.removePrefix("data:").trim()
            if (payload == "[DONE]" || !payload.startsWith("{")) continue
            quotaIn(payload)?.let { return it }
        }
        return null
    }

    private fun quotaIn(json: String): QuotaInfo? = runCatching {
        val oi = JSONObject(json).optJSONObject("otherInfo") ?: return@runCatching null
        QuotaInfo(
            total = oi.optInt("totalQuota", 0),
            used = oi.optInt("usedQuota", 0),
            internalModel = oi.optString("model", ""),
        )
    }.getOrNull()

    /** Cheap round trip used to refresh a cached quota reading. */
    fun probeQuota(uuid: String, clientId: String, model: String): Result<QuotaInfo> = runCatching {
        val saved = currentClientId
        currentClientId = clientId
        try {
            val body = JSONObject()
                .put("uuid", uuid)
                .put("model", model)
                .put("max_tokens", 1)
                .put("messages", JSONArray().put(
                    JSONObject().put("role", "user").put("content", "hi")
                ))
            val (code, text) = open("/api/ai-proxy/chat/completions", "POST", body.toString())
            if (code !in 200..299) error("HTTP $code: ${text.take(160)}")
            val oi = JSONObject(text).optJSONObject("otherInfo")
                ?: error("响应中无 otherInfo")
            QuotaInfo(
                total = oi.optInt("totalQuota", 0),
                used = oi.optInt("usedQuota", 0),
                internalModel = oi.optString("model", ""),
            )
        } finally {
            currentClientId = saved
        }
    }
}
