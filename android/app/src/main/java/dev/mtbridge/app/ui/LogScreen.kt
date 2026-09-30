package dev.mtbridge.app.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.mtbridge.app.core.LogBus
import dev.mtbridge.app.core.LogEntry
import dev.mtbridge.app.core.logDay
import dev.mtbridge.app.core.logTime
import dev.mtbridge.app.ui.icons.BugReport
import dev.mtbridge.app.ui.icons.Delete
import dev.mtbridge.app.ui.icons.ExpandLess
import dev.mtbridge.app.ui.icons.ExpandMore
import dev.mtbridge.app.ui.icons.Terminal

/**
 * 日志页。
 *
 * 结构参考 KernelSU：分组大卡片，每条是「图标 / 标题 / 辅助文字」的
 * list item，左侧带状态色条。调用记录点开可看完整请求体与响应，并复制。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LogScreen(entries: List<LogEntry>) {
    var expanded by remember { mutableStateOf<Set<Long>>(emptySet()) }
    val clipboard = LocalClipboardManager.current

    val calls = entries.count { it is LogEntry.Call }
    val lastQuota: Pair<Int, Int?>? = entries.filterIsInstance<LogEntry.Call>().lastOrNull()
        ?.let { c -> if (c.usedQuota != null) c.usedQuota to c.totalQuota else null }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("日志") },
                actions = { IconButton(onClick = { LogBus.clear() }) { Icon(Delete, "清空") } },
            )
        },
    ) { pad ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 90.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                SummaryCard(
                    total = entries.size,
                    calls = calls,
                    events = entries.size - calls,
                    quota = lastQuota,
                )
            }
            if (entries.isEmpty()) {
                item { EmptyHint("还没有日志") }
            }
            entries.groupBy { it.time.logDay() }.forEach { (day, itemsInDay) ->
                item(key = "h_$day") { DayHeader(day, itemsInDay.size) }
                items(itemsInDay, key = { it.id }) { e ->
                    EntryCard(
                        e = e,
                        open = e.id in expanded,
                        onToggle = {
                            expanded =
                                if (e.id in expanded) expanded - e.id else expanded + e.id
                        },
                        onCopy = { e.copyText?.let { clipboard.setText(AnnotatedString(it)) } },
                    )
                }
            }
        }
    }
}

@Composable
private fun SummaryCard(total: Int, calls: Int, events: Int, quota: Pair<Int, Int?>?) {
    Card(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(24.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("统计", style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onPrimaryContainer)
            Row(horizontalArrangement = Arrangement.spacedBy(26.dp)) {
                Stat("全部", total.toString())
                Stat("调用", calls.toString())
                Stat("事件", events.toString())
                if (quota != null) Stat("配额", "${quota.first}/${quota.second ?: 0}")
            }
        }
    }
}

@Composable
private fun Stat(label: String, value: String) {
    Column {
        Text(value, style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onPrimaryContainer)
        Text(label, style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onPrimaryContainer)
    }
}

@Composable
private fun DayHeader(day: String, count: Int) {
    Row(
        Modifier.fillMaxWidth().padding(top = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(day, style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.primary)
        Text("$count 条", style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun EntryCard(e: LogEntry, open: Boolean, onToggle: () -> Unit, onCopy: () -> Unit) {
    val accent = levelColor(e.level)
    val hasDetail = e.detail != null
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (hasDetail) Modifier.clickable { onToggle() } else Modifier),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Row(Modifier.height(IntrinsicSize.Min)) {
            Box(
                Modifier
                    .width(5.dp)
                    .fillMaxHeight()
                    .background(accent, RoundedCornerShape(topStart = 20.dp, bottomStart = 20.dp))
            )
            Column(
                Modifier.padding(14.dp).weight(1f),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(iconFor(e.level), null, Modifier.size(16.dp), tint = accent)
                    Spacer(Modifier.width(6.dp))
                    Text(e.time.logTime(), style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontFamily = FontFamily.Monospace)
                    Spacer(Modifier.weight(1f))
                    if (hasDetail) {
                        Icon(
                            if (open) ExpandLess else ExpandMore, null,
                            Modifier.size(18.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Text(e.title, style = MaterialTheme.typography.titleSmall,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (e.summary.isNotBlank()) {
                    Text(e.summary, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = if (open) 20 else 2,
                        overflow = TextOverflow.Ellipsis)
                }

                AnimatedVisibility(visible = open && e.detail != null) {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        HorizontalDivider()
                        Surface(
                            shape = RoundedCornerShape(12.dp),
                            color = MaterialTheme.colorScheme.surfaceContainerHighest,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Text(
                                e.detail.orEmpty(),
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace,
                                modifier = Modifier
                                    .heightIn(max = 320.dp)
                                    .horizontalScroll(rememberScrollState())
                                    .padding(10.dp),
                            )
                        }
                        if (e.copyText != null) {
                            FilledTonalButton(onClick = onCopy) { Text("复制请求与响应") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun EmptyHint(msg: String) {
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp)) {
        Text(msg, Modifier.padding(24.dp), style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private fun levelColor(l: LogEntry.Level): Color = when (l) {
    LogEntry.Level.SUCCESS -> Color(0xFF2E7D32)
    LogEntry.Level.WARN -> Color(0xFFEF6C00)
    LogEntry.Level.ERROR -> Color(0xFFC62828)
    LogEntry.Level.REQUEST -> Color(0xFF1565C0)
    LogEntry.Level.INFO -> Color(0xFF757575)
}

private fun iconFor(l: LogEntry.Level) = when (l) {
    LogEntry.Level.ERROR -> BugReport
    else -> Terminal
}
