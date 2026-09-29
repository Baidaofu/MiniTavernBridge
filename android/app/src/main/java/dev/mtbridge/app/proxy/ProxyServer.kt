package dev.mtbridge.app.proxy

import dev.mtbridge.app.core.Account
import dev.mtbridge.app.core.Bus
import dev.mtbridge.app.core.MiniTavernApi
import dev.mtbridge.app.core.QuotaInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * A minimal OpenAI-compatible reverse proxy.
 *
 * Purpose: clients such as pi and Kelivo can set custom headers but cannot
 * inject arbitrary JSON body fields, and the backend requires `uuid` in the
 * body. This server bridges that gap and also serves `GET /v1/models`, which
 * the backend itself does not expose.
 *
 * Implemented directly on ServerSocket to avoid pulling in an HTTP library.
 */
class ProxyServer(
    private val port: Int,
    private val activeAccount: () -> Account?,
    private val onModels: (String) -> Pair<String, List<MiniTavernApi.RemoteModel>>,
) {

    private var socket: ServerSocket? = null
    private val running = AtomicBoolean(false)
    private var acceptThread: Thread? = null
    private val pool = Executors.newCachedThreadPool()

    var boundPort: Int = port
        private set

    fun isRunning() = running.get()

    fun start() {
        if (running.get()) return
        val s = ServerSocket(port)
        s.reuseAddress = true
        socket = s
        boundPort = s.localPort
        running.set(true)
        acceptThread = Thread {
            while (running.get()) {
                try {
                    val client = s.accept()
                    pool.submit { handle(client) }
                } catch (t: Throwable) {
                    if (running.get()) Bus.log("accept 失败: ${t.message}")
                }
            }
        }.apply { isDaemon = true; name = "mtb-accept" }.also { it.start() }
        Bus.log("代理已启动 :$boundPort")
    }

    fun stop() {
        running.set(false)
        runCatching { socket?.close() }
        pool.shutdownNow()
        socket = null
        Bus.log("代理已停止")
    }

    // ------------------------------------------------------------- connection

    private fun handle(client: Socket) {
        client.use { c ->
            c.soTimeout = 300_000
            val input = BufferedReader(InputStreamReader(c.getInputStream(), Charsets.UTF_8))
            val requestLine = input.readLine() ?: return
            val parts = requestLine.split(" ")
            if (parts.size < 2) return
            val method = parts[0]
            val rawPath = parts[1]
            val path = rawPath.substringBefore("?")

            var contentLength = 0
            while (true) {
                val line = input.readLine() ?: break
                if (line.isEmpty()) break
                val i = line.indexOf(':')
                if (i > 0 && line.substring(0, i).trim().equals("Content-Length", true)) {
                    contentLength = line.substring(i + 1).trim().toIntOrNull() ?: 0
                }
            }
            val body = if (contentLength > 0) CharArray(contentLength).also { input.read(it) }
                .concatToString() else ""

            val out = c.getOutputStream()
            when {
                method == "OPTIONS" -> respond(out, 204, "", null)
                method == "GET" && path.endsWith("/models") -> serveModels(out, path)
                method == "GET" && path.contains("/models/") -> serveModel(out, path)
                method == "GET" && (path.endsWith("/quota") || path.endsWith("/status")) -> serveStatus(out)
                method == "POST" && path.endsWith("/chat/completions") -> serveChat(out, body)
                else -> json(out, 404, JSONObject().put("error",
                    JSONObject().put("message", "use /v1/models or /v1/chat/completions")))
            }
            out.flush()
        }
    }

    // ----------------------------------------------------------------- models

    private fun serveModels(out: java.io.OutputStream, path: String) {
        val acc = activeAccount()
        if (acc == null) {
            json(out, 503, JSONObject().put("error",
                JSONObject().put("message", "没有活动账户，请先在 App 中扫描并选择一个账户")))
            return
        }
        val (cached, models) = onModels(acc.uuid)
        if (models.isEmpty()) {
            json(out, 502, JSONObject().put("error",
                JSONObject().put("message", "无法获取上游模型列表: $cached")))
            return
        }
        val arr = JSONArray()
        models.forEach { m ->
            arr.put(JSONObject()
                .put("id", m.id)
                .put("object", "model")
                .put("created", 1788400000L)
                .put("owned_by", "minitavern")
                .put("name", m.name)
                .put("description", m.description))
        }
        json(out, 200, JSONObject().put("object", "list").put("data", arr))
    }

    private fun serveModel(out: java.io.OutputStream, path: String) {
        val acc = activeAccount() ?: return json(out, 503, JSONObject()
            .put("error", JSONObject().put("message", "没有活动账户")))
        val mid = path.substringAfterLast("/models/")
        val (_, models) = onModels(acc.uuid)
        val hit = models.firstOrNull { it.id == mid || it.name == mid }
        if (hit == null) return json(out, 404, JSONObject()
            .put("error", JSONObject().put("message", "no such model")))
        json(out, 200, JSONObject().put("id", hit.id).put("object", "model")
            .put("created", 1788400000L).put("owned_by", "minitavern")
            .put("name", hit.name).put("description", hit.description))
    }

    /** Local-only helper so other tooling can read the active account state. */
    private fun serveStatus(out: java.io.OutputStream) {
        val acc = activeAccount()
        json(out, 200, JSONObject().apply {
            put("active", acc != null)
            if (acc != null) {
                put("uuid", acc.uuid)
                put("clientId", acc.clientId)
                put("label", acc.displayName)
                put("quotaTotal", acc.quotaTotal)
                put("quotaUsed", acc.quotaUsed)
                put("quotaLeft", acc.quotaLeft)
                put("quotaUpdatedAt", acc.quotaUpdatedAt)
            }
        })
    }

    // ------------------------------------------------------------------- chat

    private fun serveChat(out: java.io.OutputStream, rawBody: String) {
        val acc = activeAccount()
        if (acc == null) {
            json(out, 503, JSONObject().put("error",
                JSONObject().put("message", "没有活动账户")))
            return
        }
        val payload = runCatching { JSONObject(rawBody) }.getOrElse {
            return json(out, 400, JSONObject().put("error",
                JSONObject().put("message", "invalid JSON body")))
        }

        val requested = payload.optString("model")
        val (_, models) = onModels(acc.uuid)
        val full = models.firstOrNull { it.id == requested || it.name == requested }?.name
            ?: requested
        payload.put("model", full)
        payload.put("uuid", acc.uuid)

        MiniTavernApi.currentClientId = acc.clientId
        val r = MiniTavernApi.chat(acc.uuid, acc.clientId, payload)
        r.onFailure { e ->
            Bus.log("请求失败: ${e.message}")
            json(out, 502, JSONObject().put("error",
                JSONObject().put("message", e.message ?: "upstream error")))
            return
        }
        val (text, quota) = r.getOrThrow()
        quota?.let {
            Bus.emitQuota(acc.uuid, it)
            Bus.log("配额 ${it.used}/${it.total} · ${it.internalModel}")
        }
        respond(out, 200, text, "application/json; charset=utf-8")
    }

    // ---------------------------------------------------------------- replies

    private fun json(out: java.io.OutputStream, code: Int, obj: JSONObject) =
        respond(out, code, obj.toString(), "application/json; charset=utf-8")

    private fun respond(
        out: java.io.OutputStream,
        code: Int,
        body: String,
        contentType: String?,
    ) {
        val b = body.toByteArray(Charsets.UTF_8)
        val sb = StringBuilder()
        sb.append("HTTP/1.1 $code ${reason(code)}\r\n")
        sb.append("Content-Type: ${contentType ?: "text/plain"}\r\n")
        sb.append("Content-Length: ${b.size}\r\n")
        sb.append("Access-Control-Allow-Origin: *\r\n")
        sb.append("Access-Control-Allow-Methods: GET,POST,OPTIONS\r\n")
        sb.append("Access-Control-Allow-Headers: *\r\n")
        sb.append("Connection: close\r\n\r\n")
        out.write(sb.toString().toByteArray(Charsets.US_ASCII))
        out.write(b)
    }

    private fun reason(code: Int) = when (code) {
        200 -> "OK"; 204 -> "No Content"; 400 -> "Bad Request"
        404 -> "Not Found"; 502 -> "Bad Gateway"; 503 -> "Service Unavailable"
        else -> "Error"
    }
}
