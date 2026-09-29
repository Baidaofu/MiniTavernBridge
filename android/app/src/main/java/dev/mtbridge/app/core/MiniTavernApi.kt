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

    /**
     * Performs one chat call. Returns the raw response body plus any quota
     * counters found in `otherInfo`.
     */
    fun chat(uuid: String, clientId: String, payload: JSONObject): Result<Pair<String, QuotaInfo?>> =
        runCatching {
            val obj = JSONObject(payload.toString())
            obj.put("uuid", uuid)
            val (code, text) = open("/api/ai-proxy/chat/completions", "POST", obj.toString())
            if (code !in 200..299) error("HTTP $code: ${text.take(300)}")
            val quota = runCatching {
                val oi = JSONObject(text).optJSONObject("otherInfo") ?: return@runCatching null
                QuotaInfo(
                    total = oi.optInt("totalQuota", 0),
                    used = oi.optInt("usedQuota", 0),
                    internalModel = oi.optString("model", ""),
                )
            }.getOrNull()
            text to quota
        }

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
