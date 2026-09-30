package dev.mtbridge.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import dev.mtbridge.app.core.Account
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 剩余配额卡。
 *
 * 检测会真的发一次请求消耗配额，所以按钮上写明了代价。
 */
@Composable
fun QuotaCard(
    account: Account?,
    probing: Boolean,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(28.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerHigh),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("剩余配额", style = MaterialTheme.typography.titleMedium)
                Text(
                    if (account != null) "${account.quotaLeft} / ${account.quotaTotal}" else "—",
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold,
                )
            }

            if (account != null && account.quotaTotal > 0) {
                val frac = (account.quotaUsed.toFloat() / account.quotaTotal).coerceIn(0f, 1f)
                LinearProgressIndicator(
                    progress = { frac },
                    modifier = Modifier.fillMaxWidth(),
                    strokeCap = androidx.compose.ui.graphics.StrokeCap.Round,
                )
            }

            if (account == null) {
                Text("尚未选择账户", style = MaterialTheme.typography.bodyMedium)
            } else {
                Text(
                    "uuid  ${account.shortUuid}",
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                )
                Text(
                    "最后更新  ${account.quotaUpdatedAt.fmtDateTime()}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            Spacer(Modifier.height(2.dp))
            Button(
                onClick = onRefresh,
                enabled = account != null && !probing,
                shape = RoundedCornerShape(20.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.secondaryContainer,
                    contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
                ),
                modifier = Modifier.fillMaxWidth(),
            ) {
                if (probing) {
                    CircularProgressIndicator(
                        Modifier.size(16.dp),
                        strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                    )
                    Text("  检测中…")
                } else {
                    Text("检测剩余配额")
                }
            }
            Text(
                "检测会实际发送一次请求，消耗 1 点配额",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

fun Long.fmtDateTime(): String {
    if (this <= 0L) return "—"
    return SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date(this))
}

fun Long.fmtRelative(): String {
    if (this <= 0L) return "从未"
    val d = System.currentTimeMillis() - this
    return when {
        d < 60_000 -> "刚刚"
        d < 3_600_000 -> "${d / 60_000} 分钟前"
        d < 86_400_000 -> "${d / 3_600_000} 小时前"
        else -> "${d / 86_400_000} 天前"
    }
}
