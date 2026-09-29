package dev.mtbridge.app.core

import java.io.BufferedReader
import java.io.InputStreamReader

/**
 * Minimal `su` shell wrapper. Every privileged operation in this app goes
 * through here so that failures are surfaced in one place.
 */
object RootShell {

    data class Result(val code: Int, val out: String, val err: String) {
        val ok: Boolean get() = code == 0
    }

    /**
     * Runs [cmd] through `su -c`. stdin is fed with [stdin] when provided,
     * which avoids shell-quoting problems for large payloads.
     */
    fun run(cmd: String, stdin: String? = null, timeoutMs: Long = 30_000): Result {
        return try {
            val p = ProcessBuilder("su", "-c", cmd)
            p.redirectErrorStream(false)
            val proc = p.start()
            if (stdin != null) {
                proc.outputStream.use { it.write(stdin.toByteArray()) }
            } else {
                proc.outputStream.close()
            }
            val out = proc.inputStream.bufferedReader().use(BufferedReader::readText)
            val err = proc.errorStream.bufferedReader().use(BufferedReader::readText)
            if (!proc.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)) {
                proc.destroy()
                return Result(-1, out, "timeout after ${timeoutMs}ms")
            }
            Result(proc.exitValue(), out, err)
        } catch (t: Throwable) {
            Result(-1, "", t.message ?: t.javaClass.simpleName)
        }
    }

    /** True when a root shell can be obtained at all. */
    fun isAvailable(): Boolean = run("id -u").out.trim() == "0"

    /** Asks the user to grant root, returning the result of the check. */
    fun requestAndCheck(): Boolean = isAvailable()

    fun readFile(path: String): String? = run("cat $path").takeIf { it.ok }?.out
}
