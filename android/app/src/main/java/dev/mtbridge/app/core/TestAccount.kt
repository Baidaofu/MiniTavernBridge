package dev.mtbridge.app.core

import org.json.JSONObject
import java.security.SecureRandom
import java.util.Base64

/**
 * 调试用：凭空造一个可用的测试账户。
 *
 * `POST /api/auth/app/bootstrap` 就是 MiniTavern 首次安装时走的自注册入口，
 * 对任意 uuid 都会开户并签发 accessToken，随后按账户发放免费配额。
 * 实测：随机 uuid 不调这一步、直接 chat 也会被自动开户（配额 100），
 * 所以 bootstrap 失败不致命，退化成"只造 uuid"仍然可用。
 *
 * 用途：手边没有多余真机账户时，验证代理链路（配额、模型、多账户切换、
 * 配额耗尽后的错误路径）。
 *
 * ⚠️ 这是服务端真实开户动作。单个用于本地调试没问题，批量生成属于滥用，
 *    可能触发风控/封号，请勿在服务端大量使用。
 */
object TestAccount {

    /** 32 位随机 hex，与后端见过的 uuid 形状一致。 */
    fun randomUuid(): String {
        val b = ByteArray(16)
        SecureRandom().nextBytes(b)
        return b.joinToString("") { "%02x".format(it) }
    }

    /**
     * 造一个账户并顺手探一次配额，方便立刻看到"新账户 0/100"。
     * 全程阻塞，调用方需放在 IO 线程。
     */
    fun provision(clientId: String): Result<Account> = runCatching {
        val uuid = randomUuid()
        val token = runCatching { MiniTavernApi.bootstrap(clientId, uuid).getOrNull() }
            .getOrNull()
        val sub = token?.let { jwtPayload(it) }?.optString("sub").orEmpty()

        // 探配额：既验证账户真的能调通，也把初始配额读回来
        val model = MiniTavernApi.fetchModels(clientId).getOrNull()?.firstOrNull()?.name
        val q = model?.let { MiniTavernApi.probeQuota(uuid, clientId, it).getOrNull() }

        Account(
            uuid = uuid,
            clientId = clientId,
            sub = sub,
            token = token.orEmpty(),
            label = "调试 ${uuid.take(6)}",
            quotaTotal = q?.total ?: 0,
            quotaUsed = q?.used ?: 0,
            quotaUpdatedAt = if (q != null) System.currentTimeMillis() else 0L,
        )
    }

    private fun jwtPayload(jwt: String): JSONObject? = runCatching {
        val part = jwt.split(".").getOrNull(1) ?: return null
        val padded = part.padEnd((part.length + 3) / 4 * 4, '=')
        JSONObject(String(Base64.getUrlDecoder().decode(padded), Charsets.UTF_8))
    }.getOrNull()
}
