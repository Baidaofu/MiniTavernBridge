package dev.mtbridge.app.proxy

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import dev.mtbridge.app.MainActivity
import dev.mtbridge.app.MtApp
import dev.mtbridge.app.R
import dev.mtbridge.app.core.Bus
import dev.mtbridge.app.core.LogBus
import dev.mtbridge.app.core.LogEntry
import dev.mtbridge.app.core.MiniTavernApi
import dev.mtbridge.app.core.QuotaInfo

/**
 * Foreground service hosting [ProxyServer]. Keeping the proxy in a foreground
 * service means it survives the app being backgrounded, which matters because
 * editor clients keep long-lived connections.
 */
class ProxyService : Service() {

    companion object {
        const val ACTION_START = "dev.mtbridge.app.START"
        const val ACTION_STOP = "dev.mtbridge.app.STOP"
        private const val CHANNEL = "proxy"
        private const val NOTIF_ID = 1001

        fun start(ctx: Context, port: Int) {
            val i = Intent(ctx, ProxyService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, ProxyService::class.java).setAction(ACTION_STOP))
        }
    }

    private var server: ProxyServer? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                createChannel()
                startForeground(NOTIF_ID, buildNotification("启动中…"))
                startProxy()
            }
        }
        return START_STICKY
    }

    private fun startProxy() {
        val app = applicationContext as MtApp
        // 重复 START 时先停掉上一个实例，否则端口被自己占住，
        // 第二次 bind 会抛 EADDRINUSE 把整个进程带崩。
        server?.stop()
        server = null
        val port = app.settings.value.port
        val s = ProxyServer(
            port = port,
            activeAccount = { app.store.active },
            onModels = { uuid -> app.cachedModels(uuid) },
        )
        if (!s.start()) {
            // 常见原因：另一个 mtbridge（debug/release 两份）正占着 8787。
            updateNotification("启动失败 · 端口 $port 不可用")
            stopSelf()
            return
        }
        server = s
        val acc = app.store.active
        updateNotification(
            if (acc == null) "已启动 · 未选择账户"
            else "已启动 · ${acc.displayName} · :${s.boundPort}"
        )
    }

    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "本地代理", NotificationManager.IMPORTANCE_LOW)
        )
    }

    private fun updateNotification(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIF_ID, buildNotification(text))
    }

    private fun buildNotification(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("MiniTavern Bridge")
            .setContentText(text)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    override fun onDestroy() {
        LogBus.event("代理已停止", "端口 ${server?.boundPort ?: "-"}", LogEntry.Level.WARN)
        server?.stop()
        server = null
        super.onDestroy()
    }
}
