package dev.mtbridge.app.proxy

import dev.mtbridge.app.core.Account
import dev.mtbridge.app.core.Bus
import dev.mtbridge.app.core.LogBus
import dev.mtbridge.app.core.LogEntry
import dev.mtbridge.app.core.MiniTavernApi
import dev.mtbridge.app.core.QuotaInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Upper bound for the head section, to keep a hostile peer from stashing us. */
private const val MAX_HEAD_BYTES = 64 * 1024

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

    /** Starts listening. Returns false when the port is taken (no crash). */
    fun start(): Boolean {
        if (running.get()) return true
        val s = try {
            ServerSocket().apply {
                reuseAddress = true
                bind(java.net.InetSocketAddress(port))
            }
        } catch (e: Throwable) {
            Bus.log("代理启动失败 :$port — ${e.message}")
            return false
        }
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
        return true
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
            val req = readRequest(c.getInputStream()) ?: return
            val path = req.path.substringBefore("?")

            val out = c.getOutputStream()
            when {
                req.method == "OPTIONS" -> respond(out, 204, "", null)
                req.method == "GET" && path.endsWith("/models") -> serveModels(out, path)
                req.method == "GET" && path.contains("/models/") -> serveModel(out, path)
                req.method == "GET" && (path.endsWith("/quota") || path.endsWith("/status")) ->
                    serveStatus(out)
                req.method == "POST" && path.endsWith("/chat/completions") ->
                    serveChat(out, req.body)
                else -> json(out, 404, JSONObject().put("error",
                    JSONObject().put("message", "use /v1/models or /v1/chat/completions")))
            }
            out.flush()
        }
    }

    private class Request(val method: String, val path: String, val body: String)

    /**
     * Reads one HTTP request **entirely at byte level**.
     *
     * Why not a char reader: looping until `contentLength` *characters* have been
     * consumed deadlocks on any multi-byte body — 100 bytes of UTF-8 Chinese text
     * decode to ~97 chars, so the loop waits forever for 3 characters that will
     * never arrive (the upstream keeps the connection open awaiting our reply).
     * Split headers/body in bytes and decode exactly once instead.
     */
    private fun readRequest(raw: InputStream): Request? {
        val head = ByteArrayOutputStream()
        val tmp = ByteArray(4096)
        var bodyAt = -1
        while (bodyAt < 0) {
            val n = raw.read(tmp)
            if (n < 0) return null
            head.write(tmp, 0, n)
            val arr = head.toByteArray()
            bodyAt = headerEnd(arr)
            if (bodyAt < 0 && arr.size > MAX_HEAD_BYTES) return null
        }

        val all = head.toByteArray()
        val lines = String(all, 0, bodyAt, Charsets.UTF_8).split("\r\n", "\n")
        val parts = lines.firstOrNull()?.split(" ") ?: return null
        if (parts.size < 2) return null

        var contentLength = 0
        for (i in 1 until lines.size) {
            val idx = lines[i].indexOf(':')
            if (idx > 0 && lines[i].substring(0, idx).trim().equals("Content-Length", true)) {
                contentLength = lines[i].substring(idx + 1).trim().toIntOrNull() ?: 0
            }
        }

        val body = ByteArrayOutputStream()
        if (all.size > bodyAt) body.write(all, bodyAt, all.size - bodyAt)
        while (body.size() < contentLength) {
            val n = raw.read(tmp, 0, minOf(tmp.size, contentLength - body.size()))
            if (n < 0) break
            body.write(tmp, 0, n)
        }
        val bytes = body.toByteArray()
        val text = String(bytes, 0, minOf(bytes.size, contentLength), Charsets.UTF_8)
        return Request(parts[0], parts[1], text)
    }

    /** Index just past the blank line that ends the head, or -1. Handles CRLF and LF. */
    private fun headerEnd(b: ByteArray): Int {
        for (i in 0 until b.size - 1) {
            if (b[i] == 10.toByte() && b[i + 1] == 10.toByte()) return i + 2
        }
        val crlf = byteArrayOf(13, 10, 13, 10)
        for (i in 0..b.size - crlf.size) {
            var hit = true
            for (j in crlf.indices) if (b[i + j] != crlf[j]) { hit = false; break }
            if (hit) return i + crlf.size
        }
        return -1
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
                // id 用后端真实全名（如 deepseek/deepseek-v3.2-exp），
                // 第三方软件直接看到可读模型名；短 id 仍可用于发请求
                // （serveChat 双向匹配），额外挂在 mtb_id 上方便排查。
                .put("id", m.name)
                .put("object", "model")
                .put("created", 1788400000L)
                .put("owned_by", "minitavern")
                .put("name", m.name)
                .put("mtb_id", m.id)
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
        json(out, 200, JSONObject().put("id", hit.name).put("object", "model")
            .put("created", 1788400000L).put("owned_by", "minitavern")
            .put("name", hit.name).put("mtb_id", hit.id)
            .put("description", hit.description))
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
        val wantStream = payload.optBoolean("stream", false)

        MiniTavernApi.currentClientId = acc.clientId

        // 记录本次调用的完整上下文，结束时写入结构化日志
        val ctx = CallCtx(
            model = full,
            stream = wantStream,
            account = acc,
            requestBody = prettyJson(payload),
        )
        val t0 = System.nanoTime()

        var opened = MiniTavernApi.openChat(acc.uuid, acc.clientId, payload)
        if (wantStream && opened.isFailure) {
            // 上游可能不认 stream 标志：去掉标志重试，再由本地切成 SSE。
            Bus.log("上游拒绝 stream 参数，回退为非流式并在本地切片: " +
                opened.exceptionOrNull()?.message)
            payload.remove("stream")
            opened = MiniTavernApi.openChat(acc.uuid, acc.clientId, payload)
        }
        val up = opened.getOrElse { e ->
            val status = (e as? MiniTavernApi.UpstreamHttpError)?.code ?: 502
            ctx.finish(t0, status, error = e.message ?: "upstream error")
            // 上游给了明确状态码（配额不足=400、认证失败=401…）就原样透出，
            // 否则才归为 502。
            return json(out, status, JSONObject().put("error",
                JSONObject().put("message", e.message ?: "upstream error")))
        }

        up.use {
            when {
                // 1) 上游真流式：逐行透传
                wantStream && up.isEventStream ->
                    relaySse(out, acc, up, requested, ctx, t0)
                // 2) 上游只给整包 JSON：本地切成 SSE
                wantStream ->
                    syntheticSse(out, acc, up, requested, ctx, t0)
                // 3) 客户端要 JSON、上游给 SSE：累积成一次完整响应
                up.isEventStream ->
                    accumulatedJson(out, acc, up, requested, ctx, t0)
                // 4) 普通非流式
                else ->
                    plainJson(out, acc, up, ctx, t0)
            }
        }
    }

    /** 一次调用从发起到结束的上下文，用于产出结构化日志。 */
    private class CallCtx(
        val model: String,
        val stream: Boolean,
        val account: Account,
        val requestBody: String,
    ) {
        private val response = StringBuilder()
        fun append(s: String) {
            if (response.length < LogBus.MAX_TEXT) response.append(s)
        }

        @Synchronized
        fun finish(t0: Long, status: Int?, error: String? = null) {
            LogBus.call(
                model = model,
                stream = stream,
                accountLabel = account.displayName,
                accountUuid = account.uuid,
                latencyMs = (System.nanoTime() - t0) / 1_000_000,
                status = status,
                requestBody = requestBody,
                responseBody = response.toString().ifBlank { "(空)" },
                error = error,
            )
        }
    }

    private fun prettyJson(o: JSONObject): String = runCatching {
        o.toString(2)
    }.getOrElse { o.toString() }

    // ---------------------------------------------------------------- replies

    private fun plainJson(
        out: java.io.OutputStream,
        acc: Account,
        up: MiniTavernApi.ChatUpstream,
        ctx: CallCtx,
        t0: Long,
    ) {
        val text = up.body.bufferedReader(Charsets.UTF_8).use { it.readText() }
        reportQuota(acc, MiniTavernApi.quotaOf(text))
        ctx.append(text)
        ctx.append(text)
        ctx.finish(t0, 200)
        respond(out, 200, text, "application/json; charset=utf-8")
    }

    /** Upstream speaks SSE: pass every event straight through, as it arrives. */
    private fun relaySse(
        out: java.io.OutputStream,
        acc: Account,
        up: MiniTavernApi.ChatUpstream,
        model: String,
        ctx: CallCtx,
        t0: Long,
    ) {
        writeSseHead(out)
        val chunked = Chunked(out)
        val reader = up.body.bufferedReader(Charsets.UTF_8)
        val event = StringBuilder()
        var quota: QuotaInfo? = null
        var sawDone = false
        while (true) {
            val line = reader.readLine() ?: break
            if (line.isEmpty()) {
                // 一个 SSE 事件结束（空行分隔）
                if (event.isNotEmpty()) {
                    chunked.write((event.toString() + "\r\n").toByteArray(Charsets.UTF_8))
                    quota = MiniTavernApi.quotaOf(event.toString()) ?: quota
                    if (event.contains("[DONE]")) sawDone = true
                    event.setLength(0)
                }
            } else {
                event.append(line).append('\n')
            }
        }
        if (event.isNotEmpty()) {
            chunked.write((event.toString() + "\r\n").toByteArray(Charsets.UTF_8))
            quota = MiniTavernApi.quotaOf(event.toString()) ?: quota
            if (event.contains("[DONE]")) sawDone = true
        }
        // 上游经常不发终止符就直接断开（实测 monitor.mini-tavern.com 就不发），
        // 严格按 OpenAI 语义消费的客户端会一直等到超时，这里补齐。
        if (!sawDone) {
            chunked.write("data: [DONE]\n\n".toByteArray(Charsets.UTF_8))
        }
        chunked.finish()
        reportQuota(acc, quota)
        ctx.finish(t0, 200)
    }

    /**
     * Upstream answered with one complete JSON body even though the client asked
     * for a stream. Cut it into `chat.completion.chunk` events so the client's
     * SSE parser still sees a normal incremental stream.
     */
    private fun syntheticSse(
        out: java.io.OutputStream,
        acc: Account,
        up: MiniTavernApi.ChatUpstream,
        model: String,
        ctx: CallCtx,
        t0: Long,
    ) {
        val text = up.body.bufferedReader(Charsets.UTF_8).use { it.readText() }
        reportQuota(acc, MiniTavernApi.quotaOf(text))
        ctx.append(text)

        writeSseHead(out)
        val chunked = Chunked(out)
        val root = runCatching { JSONObject(text) }.getOrNull()
        val id = root?.optString("id").orEmpty().ifBlank { "chatcmpl-mtb" }
        val created = (root?.optLong("created") ?: 0L).takeIf { it > 0 }
            ?: System.currentTimeMillis() / 1000
        val outModel = root?.optString("model").orEmpty().ifBlank { model }
        val usage = root?.optJSONObject("usage")

        // choices → 每个 index 的全文
        val contents = LinkedHashMap<Int, String>()
        val finishes = LinkedHashMap<Int, String>()
        val choices = root?.optJSONArray("choices")
        if (choices != null) {
            for (i in 0 until choices.length()) {
                val c = choices.optJSONObject(i) ?: continue
                val idx = c.optInt("index", i)
                val msg = c.optJSONObject("message")
                contents[idx] = (msg?.optString("content").orEmpty().ifBlank {
                    c.optString("text")
                }).ifBlank { text }
                finishes[idx] = c.optString("finish_reason").ifBlank { "stop" }
            }
        }
        if (contents.isEmpty()) {
            // 结构不认识：把整包文本作为一个 delta 交出去，客户端至少能收到内容
            contents[0] = text
            finishes[0] = "stop"
        }

        fun emit(choicesArr: JSONArray) {
            val obj = JSONObject()
                .put("id", id)
                .put("object", "chat.completion.chunk")
                .put("created", created)
                .put("model", outModel)
                .put("choices", choicesArr)
            chunked.write("data: ${obj}\n\n".toByteArray(Charsets.UTF_8))
        }

        // 1) 首包：role
        emit(JSONArray().also { arr ->
            contents.keys.forEach { idx ->
                arr.put(JSONObject().put("index", idx)
                    .put("delta", JSONObject().put("role", "assistant").put("content", ""))
                    .put("finish_reason", JSONObject.NULL))
            }
        })

        // 2) 正文：按轮次切片，多个 choice 同步推进
        val pieces = contents.mapValues { (_, v) -> splitPieces(v, 64) }
        val rounds = pieces.values.maxOfOrNull { it.size } ?: 0
        for (r in 0 until rounds) {
            val arr = JSONArray()
            for ((idx, list) in pieces) {
                if (r >= list.size) continue
                arr.put(JSONObject().put("index", idx)
                    .put("delta", JSONObject().put("content", list[r]))
                    .put("finish_reason", JSONObject.NULL))
            }
            if (arr.length() > 0) emit(arr)
        }

        // 3) 收尾：finish_reason
        emit(JSONArray().also { arr ->
            finishes.forEach { (idx, reason) ->
                arr.put(JSONObject().put("index", idx)
                    .put("delta", JSONObject())
                    .put("finish_reason", reason))
            }
        })

        // 4) usage 单独一个包（choices 为空），与 OpenAI 的 stream 语义一致
        if (usage != null) {
            val last = JSONObject()
                .put("id", id).put("object", "chat.completion.chunk")
                .put("created", created).put("model", outModel)
                .put("choices", JSONArray())
                .put("usage", usage)
            chunked.write("data: ${last}\n\n".toByteArray(Charsets.UTF_8))
        }

        chunked.write("data: [DONE]\n\n".toByteArray(Charsets.UTF_8))
        chunked.finish()
    }

    /** Upstream streamed, but the client asked for plain JSON: stitch it back. */
    private fun accumulatedJson(
        out: java.io.OutputStream,
        acc: Account,
        up: MiniTavernApi.ChatUpstream,
        model: String,
        ctx: CallCtx,
        t0: Long,
    ) {
        val raw = up.body.bufferedReader(Charsets.UTF_8).use { it.readText() }
        if (!raw.contains("data:")) {
            // 声称是 SSE 却不是 → 当普通 JSON 交出去
            reportQuota(acc, MiniTavernApi.quotaOf(raw))
            ctx.append(raw)
            ctx.finish(t0, 200)
            return respond(out, 200, raw, "application/json; charset=utf-8")
        }

        var id = ""
        var created = 0L
        var outModel = ""
        var finish = "stop"
        var usage: JSONObject? = null
        var otherInfo: JSONObject? = null
        val contents = LinkedHashMap<Int, StringBuilder>()

        for (line in raw.lineSequence()) {
            val payload = line.takeIf { it.startsWith("data:") }
                ?.removePrefix("data:")?.trim() ?: continue
            if (payload == "[DONE]") continue
            val o = runCatching { JSONObject(payload) }.getOrNull() ?: continue
            if (o.has("id") && id.isBlank()) id = o.optString("id")
            if (o.has("created") && created == 0L) created = o.optLong("created")
            if (o.has("model") && outModel.isBlank()) outModel = o.optString("model")
            o.optJSONObject("usage")?.let { usage = it }
            o.optJSONObject("otherInfo")?.let { otherInfo = it }
            val arr = o.optJSONArray("choices") ?: continue
            for (i in 0 until arr.length()) {
                val c = arr.optJSONObject(i) ?: continue
                val idx = c.optInt("index", i)
                c.optString("finish_reason").takeIf { it.isNotBlank() }?.let { finish = it }
                val delta = c.optJSONObject("delta") ?: continue
                val piece = delta.optString("content")
                if (piece.isNotEmpty()) {
                    contents.getOrPut(idx) { StringBuilder() }.append(piece)
                }
            }
        }

        val response = JSONObject()
            .put("id", id.ifBlank { "chatcmpl-mtb" })
            .put("object", "chat.completion")
            .put("created", if (created > 0) created else System.currentTimeMillis() / 1000)
            .put("model", outModel.ifBlank { model })
            .put("choices", JSONArray().also { arr ->
                if (contents.isEmpty()) {
                    arr.put(JSONObject().put("index", 0)
                        .put("message", JSONObject().put("role", "assistant").put("content", ""))
                        .put("finish_reason", finish))
                } else {
                    contents.forEach { (idx, sb) ->
                        arr.put(JSONObject().put("index", idx)
                            .put("message", JSONObject().put("role", "assistant")
                                .put("content", sb.toString()))
                            .put("finish_reason", finish))
                    }
                }
            })
        usage?.let { response.put("usage", it) }
        otherInfo?.let { response.put("otherInfo", it) }

        val quota = otherInfo?.let {
            QuotaInfo(
                total = it.optInt("totalQuota", 0),
                used = it.optInt("usedQuota", 0),
                internalModel = it.optString("model", ""),
            )
        }
        reportQuota(acc, quota)
        ctx.append(response.toString())
        ctx.finish(t0, 200)
        respond(out, 200, response.toString(), "application/json; charset=utf-8")
    }

    // ------------------------------------------------------------ sse plumbing

    private fun writeSseHead(out: java.io.OutputStream) {
        val sb = StringBuilder()
        sb.append("HTTP/1.1 200 OK\r\n")
        sb.append("Content-Type: text/event-stream; charset=utf-8\r\n")
        sb.append("Cache-Control: no-cache\r\n")
        sb.append("Connection: close\r\n")
        sb.append("Transfer-Encoding: chunked\r\n")
        sb.append("Access-Control-Allow-Origin: *\r\n")
        sb.append("Access-Control-Allow-Methods: GET,POST,OPTIONS\r\n")
        sb.append("Access-Control-Allow-Headers: *\r\n\r\n")
        out.write(sb.toString().toByteArray(Charsets.US_ASCII))
        out.flush()
    }

    /** Minimal HTTP/1.1 chunked writer — every call is flushed immediately. */
    private class Chunked(private val out: java.io.OutputStream) {
        fun write(bytes: ByteArray) {
            out.write("${Integer.toHexString(bytes.size)}\r\n".toByteArray(Charsets.US_ASCII))
            out.write(bytes)
            out.write("\r\n".toByteArray(Charsets.US_ASCII))
            out.flush()
        }

        fun finish() {
            out.write("0\r\n\r\n".toByteArray(Charsets.US_ASCII))
            out.flush()
        }
    }

    /** Split text into pieces without ever breaking a surrogate pair. */
    private fun splitPieces(s: String, size: Int): List<String> {
        if (s.isEmpty()) return emptyList()
        val res = ArrayList<String>()
        var i = 0
        while (i < s.length) {
            var end = minOf(i + size, s.length)
            if (end < s.length && Character.isHighSurrogate(s[end - 1]) &&
                Character.isLowSurrogate(s[end])
            ) end++
            res.add(s.substring(i, end))
            i = end
        }
        return res
    }

    private fun reportQuota(acc: Account, quota: QuotaInfo?) {
        quota?.let {
            Bus.emitQuota(acc.uuid, it)
            Bus.log("配额 ${it.used}/${it.total} · ${it.internalModel}")
        }
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
