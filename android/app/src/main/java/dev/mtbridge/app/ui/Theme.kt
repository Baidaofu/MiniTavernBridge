package dev.mtbridge.app.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

private val Teal = Color(0xFF00695C)
private val TealLight = Color(0xFF6FD9C6)
private val Sand = Color(0xFF7A5900)

/**
 * MD3 Expressive 形状体系。
 *
 * 相比传统 MD3 的 12/16dp 圆角，E 版把大面积容器推到 28dp、
 * 小控件 20dp、按钮全圆角，配合 surfaceContainer 层级代替分割线。
 */
val M3EShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(20.dp),
    large = RoundedCornerShape(28.dp),
    extraLarge = RoundedCornerShape(36.dp),
)

/** 常用尺寸，供各页面直接引用。 */
object Shape {
    val card: Dp @Composable get() = 28.dp
    val inner: Dp @Composable get() = 20.dp
    val chip: Dp @Composable get() = 16.dp
    val button: Dp @Composable get() = 20.dp
}

private val LightColors = lightColorScheme(
    primary = Teal,
    onPrimary = Color.White,
    primaryContainer = Color(0xFF9EF2E0),
    onPrimaryContainer = Color(0xFF00201C),
    secondary = Color(0xFF4A6360),
    secondaryContainer = Color(0xFFCCE8E2),
    onSecondaryContainer = Color(0xFF06201D),
    tertiary = Sand,
    tertiaryContainer = Color(0xFFFFDF9B),
    onTertiaryContainer = Color(0xFF261A00),
    error = Color(0xFFBA1A1A),
    errorContainer = Color(0xFFFFDAD6),
    surface = Color(0xFFFBFDF9),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF5F7F2),
    surfaceContainer = Color(0xFFEFF1EC),
    surfaceContainerHigh = Color(0xFFE9ECE7),
    surfaceContainerHighest = Color(0xFFE3E6E1),
)

private val DarkColors = darkColorScheme(
    primary = TealLight,
    onPrimary = Color(0xFF003731),
    primaryContainer = Color(0xFF005048),
    onPrimaryContainer = Color(0xFF9EF2E0),
    secondary = Color(0xFFB1CCC8),
    secondaryContainer = Color(0xFF334B48),
    onSecondaryContainer = Color(0xFFCCE8E2),
    tertiary = Color(0xFFF5BF48),
    tertiaryContainer = Color(0xFF5C4300),
    onTertiaryContainer = Color(0xFFFFDF9B),
    error = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF93000A),
    surface = Color(0xFF0E1512),
    surfaceContainerLowest = Color(0xFF090F0D),
    surfaceContainerLow = Color(0xFF161D1A),
    surfaceContainer = Color(0xFF1A211E),
    surfaceContainerHigh = Color(0xFF242B28),
    surfaceContainerHighest = Color(0xFF2F3633),
)

@Composable
fun MiniTavernBridgeTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = true,
    content: @Composable () -> Unit,
) {
    val ctx = LocalContext.current
    val colors = when {
        dynamicColor && android.os.Build.VERSION.SDK_INT >= 31 ->
            if (darkTheme) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        darkTheme -> DarkColors
        else -> LightColors
    }
    MaterialTheme(colorScheme = colors, shapes = M3EShapes, content = content)
}
