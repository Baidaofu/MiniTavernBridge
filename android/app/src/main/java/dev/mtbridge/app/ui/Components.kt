package dev.mtbridge.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
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

@Composable
fun QuotaCard(account: Account?, modifier: Modifier = Modifier) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("剩余配额", style = MaterialTheme.typography.titleMedium)
                if (account != null) {
                    Text(
                        "${account.quotaLeft} / ${account.quotaTotal}",
                        style = MaterialTheme.typography.headlineSmall,
                        fontWeight = FontWeight.Bold,
                    )
                } else {
                    Text("—", style = MaterialTheme.typography.headlineSmall)
                }
            }

            if (account != null && account.quotaTotal > 0) {
                val frac = (account.quotaUsed.toFloat() / account.quotaTotal).coerceIn(0f, 1f)
                LinearProgressIndicator(
                    progress = { frac },
                    modifier = Modifier.fillMaxWidth(),
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
                Text(
                    // The backend exposes no quota-reset endpoint (getAdQuota
                    // returns quota:0 time:0), so this is observational only.
                    "重置时间  后端未提供该接口",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
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
