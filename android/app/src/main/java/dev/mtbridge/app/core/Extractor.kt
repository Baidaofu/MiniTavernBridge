package dev.mtbridge.app.core

import android.util.Base64
import org.json.JSONObject

/**
 * Reads MiniTavern account material off a rooted device.
 *
 * Why memory scanning: the app stores everything encrypted.
 *   - files/settings/apiSettings.json  -> OpenSSL "Salted__" AES blob
 *   - files/users/userInfo.json         -> same
 *   - shared_prefs/SecureStore.xml      -> EncryptedSharedPreferences
 * The AES password lives in Android Keystore and is not extractable, so the
 * on-disk files cannot be read statically.
 *
 * The Hermes JS heap, however, holds the *decrypted* forms as UTF-16 strings.
 * The session JWT in particular carries the account's `uuid` and `clientId`,
 * which is the only credential the backend actually checks.
 */
class Extractor(
    private val targetPkg: String = "com.kongyouwangluo.minitavern",
) {

    data class ScanResult(
        val accounts: List<Account>,
        val pid: Int?,
        val scannedBytes: Long,
        val note: String,
    )

    private val jwtRe = Regex("eyJ[A-Za-z0-9_-]+\\.eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+")

    fun isInstalled(): Boolean =
        RootShell.run("pm list packages $targetPkg").out.contains(targetPkg)

    fun pid(): Int? = RootShell.run("pidof $targetPkg")
        .out.trim().split(Regex("\\s+")).firstOrNull()?.toIntOrNull()

    fun launch() {
        RootShell.run("monkey -p $targetPkg -c android.intent.category.LAUNCHER 1")
    }

    /** Waits for the app process to come up, launching it if needed. */
    fun ensureRunning(timeoutSec: Int = 25): Int? {
        pid()?.let { return it }
        if (!isInstalled()) return null
        launch()
        repeat(timeoutSec) {
            Thread.sleep(1000)
            pid()?.let { p -> return p }
        }
        return null
    }

    /**
     * Locates the readable Hermes heap mappings. Hermes names them
     * `anon:hades-segment` (the GC'd object heap) and `anon:hermes-rt`.
     */
    private fun heapRegions(pid: Int): List<LongRange> {
        val maps = RootShell.run("cat /proc/$pid/maps").out
        val out = mutableListOf<LongRange>()
        maps.lineSequence().forEach { line ->
            val m = Regex("^([0-9a-f]+)-([0-9a-f]+) (\\S{4}) \\S+ \\S+ \\S+\\s*(.*)$")
                .matchEntire(line.trim()) ?: return@forEach
            val perms = m.groupValues[3]
            val path = m.groupValues[4]
            if (!perms.contains('r')) return@forEach
            if (!path.contains("hades") && !path.contains("hermes")) return@forEach
            val start = m.groupValues[1].toLong(16)
            val end = m.groupValues[2].toLong(16)
            out += start until end
        }
        return out
    }

    /**
     * Pulls the process blob back as bytes. RootShell.run returns a String, so
     * binary is transferred through ISO-8859-1 which is a 1:1 byte mapping.
     * Regions are written to separate files first because toybox `dd` has no
     * `oflag=append`, then concatenated.
     */
    private fun fetchHeapBytes(pid: Int, regions: List<LongRange>): ByteArray {
        val dir = "/data/local/tmp/mtb_dump"
        val script = buildString {
            append("#!/system/bin/sh\n")
            append("D=$dir\nrm -rf \$D; mkdir -p \$D\n")
            regions.forEachIndexed { i, r ->
                append("dd if=/proc/$pid/mem of=\$D/$i.bin bs=4096 skip=${r.first / 4096} count=${(r.last - r.first) / 4096} 2>/dev/null\n")
            }
            append("cat \$D/*.bin > \$D/all.bin 2>/dev/null\nrm -f \$D/*.bin\necho \$D/all.bin\n")
        }
        RootShell.run("cat > $dir.sh", stdin = script)
        RootShell.run("chmod 755 $dir.sh")
        val path = RootShell.run("sh $dir.sh", timeoutMs = 180_000)
            .out.trim().lines().lastOrNull { it.endsWith("all.bin") } ?: return ByteArray(0)
        val raw = RootShell.run("cat $path", timeoutMs = 180_000).out
        RootShell.run("rm -rf $dir $dir.sh")
        return raw.toByteArray(Charsets.ISO_8859_1)
    }

    fun scan(): ScanResult {
        if (!RootShell.isAvailable()) {
            return ScanResult(emptyList(), null, 0, "未获得 root 权限")
        }
        if (!isInstalled()) {
            return ScanResult(emptyList(), null, 0, "未安装 $targetPkg")
        }
        val pid = ensureRunning() ?: return ScanResult(emptyList(), null, 0, "App 进程未启动")
        val regions = heapRegions(pid)
        if (regions.isEmpty()) return ScanResult(emptyList(), pid, 0, "未定位到 Hermes 堆区段")

        Bus.log("堆区段 ${regions.size} 个，开始转储…")
        val blob = fetchHeapBytes(pid, regions)
        Bus.log("转储 ${blob.size / 1024 / 1024} MB，开始解析")

        val seen = LinkedHashMap<String, Account>()
        // Hermes stores JS strings as UTF-16.
        for (encoding in listOf(Charsets.UTF_16LE, Charsets.ISO_8859_1)) {
            val text = String(blob, encoding)
            for (m in jwtRe.findAll(text)) {
                val acc = parseJwt(m.value) ?: continue
                val prev = seen[acc.uuid]
                // keep the one with the furthest expiry
                if (prev == null || acc.tokenExp > prev.tokenExp) seen[acc.uuid] = acc
            }
        }

        Bus.log("解析到 ${seen.size} 个不同账户")
        return ScanResult(
            accounts = seen.values.sortedByDescending { it.tokenExp },
            pid = pid,
            scannedBytes = blob.size.toLong(),
            note = "扫描 ${blob.size / 1024 / 1024} MB，命中 ${seen.size} 个账户",
        )
    }

    private fun parseJwt(jwt: String): Account? = runCatching {
        val parts = jwt.split(".")
        if (parts.size < 2) return null
        val payload = String(Base64.decode(parts[1], Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING))
        val o = JSONObject(payload)
        val uuid = o.optString("uuid").takeIf { it.length >= 16 } ?: return null
        val clientId = o.optString("clientId")
            .takeIf { it.length >= 8 }
            ?: Constants.CLIENT_ID_FALLBACK
        Account(
            uuid = uuid,
            clientId = clientId,
            sub = o.optString("sub"),
            token = jwt,
            tokenExp = o.optLong("exp") * 1000L,
        )
    }.getOrNull()
}

object Constants {
    /** Observed to be identical across every app install. */
    const val CLIENT_ID_FALLBACK = "68cd199d61947054fdf25ebe"
    const val UPSTREAM = "https://monitor.mini-tavern.com"
    const val PROXY_CHAT = "$UPSTREAM/api/ai-proxy/chat/completions"
    const val UPSTREAM_MODELS = "$UPSTREAM/api/api-keys/list"
}
