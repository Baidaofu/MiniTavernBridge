package dev.mtbridge.app.ui.icons

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/**
 * 本项目用到的 Material 图标，就地定义。
 *
 * 起因：androidx.compose.material:material-icons-extended 打包进来 34 MB
 * （未压缩），而本项目实际只用到 15 个图标。换成自己定义后 APK 从
 * 15.7 MB 降到 3 MB 级别。
 *
 * 路径数据取自 Material Icons（Apache-2.0），24dp 视口。
 */

private fun icon(name: String, pathData: String): ImageVector =
    ImageVector.Builder(
        name = name,
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    ).addPath(
        pathData = addPathNodes(pathData),
        fill = SolidColor(Color.Black),
    ).build()

val Add: ImageVector get() = icon(
    "Add", "M19,13h-6v6h-2v-6H5v-2h6V5h2v6h6V13z")

val BugReport: ImageVector get() = icon(
    "BugReport",
    "M20,8h-2.81c-0.45,-0.78 -1.07,-1.45 -1.82,-1.96L17,4.41 15.59,3l-2.17,2.17 " +
        "C12.96,4.99 12.5,4.9 12,4.9s-0.96,0.09 -1.41,0.27L8.41,3 7,4.41l1.62,1.63 " +
        "C7.88,6.55 7.26,7.22 6.81,8H4v2h2.09C6,10.33 6,10.66 6,11v1H4v2h2v1 " +
        "c0,0.34 0.04,0.67 0.09,1H4v2h2.81c1.04,1.79 2.97,3 5.19,3s4.15,-1.21 5.19,-3H20v-2h-2.09 " +
        "c0.05,-0.33 0.09,-0.66 0.09,-1v-1h2v-2h-2v-1c0,-0.34 -0.04,-0.67 -0.09,-1H20V8z " +
        "M14,16h-4v-2h4V16zM14,12h-4v-2h4V12z")

val Delete: ImageVector get() = icon(
    "Delete",
    "M6,19c0,1.1 0.9,2 2,2h8c1.1,0 2,-0.9 2,-2V7H6v12zM19,4h-3.5l-1,-1h-5l-1,1H5v2h14V4z")

val Download: ImageVector get() = icon(
    "Download",
    "M19,9h-4V3H9v6H5l7,7 7,-7zM5,18v2h14v-2H5z")

val Edit: ImageVector get() = icon(
    "Edit",
    "M3,17.25V21h3.75L17.81,9.94l-3.75,-3.75L3,17.25zM20.71,7.04c0.39,-0.39 0.39,-1.02 0,-1.41 " +
        "l-2.34,-2.34c-0.39,-0.39 -1.02,-0.39 -1.41,0l-1.83,1.83 3.75,3.75 1.83,-1.83z")

val Home: ImageVector get() = icon(
    "Home", "M10,20v-6h4v6h5v-8h3L12,3 2,12h3v8z")

val Person: ImageVector get() = icon(
    "Person",
    "M12,12c2.21,0 4,-1.79 4,-4s-1.79,-4 -4,-4 -4,1.79 -4,4 1.79,4 4,4z " +
        "M12,14c-2.67,0 -8,1.34 -8,4v2h16v-2c0,-2.66 -5.33,-4 -8,-4z")

val PlayArrow: ImageVector get() = icon(
    "PlayArrow", "M8,5v14l11,-7z")

val Radar: ImageVector get() = icon(
    "Radar",
    "M12,2C6.48,2 2,6.48 2,12s4.48,10 10,10 10,-4.48 10,-10S17.52,2 12,2z " +
        "M12,20c-4.41,0 -8,-3.59 -8,-8s3.59,-8 8,-8 8,3.59 8,8 -3.59,8 -8,8z " +
        "M12,7c-2.76,0 -5,2.24 -5,5s2.24,5 5,5 5,-2.24 5,-5 -2.24,-5 -5,-5z " +
        "M12,10c-1.1,0 -2,0.9 -2,2s0.9,2 2,2 2,-0.9 2,-2 -0.9,-2 -2,-2z")

val RadioButtonChecked: ImageVector get() = icon(
    "RadioButtonChecked",
    "M12,7c-2.76,0 -5,2.24 -5,5s2.24,5 5,5 5,-2.24 5,-5 -2.24,-5 -5,-5z " +
        "M12,2C6.48,2 2,6.48 2,12s4.48,10 10,10 10,-4.48 10,-10S17.52,2 12,2z " +
        "M12,20c-4.41,0 -8,-3.59 -8,-8s3.59,-8 8,-8 8,3.59 8,8 -3.59,8 -8,8z")

val RadioButtonUnchecked: ImageVector get() = icon(
    "RadioButtonUnchecked",
    "M12,2C6.48,2 2,6.48 2,12s4.48,10 10,10 10,-4.48 10,-10S17.52,2 12,2z " +
        "M12,20c-4.41,0 -8,-3.59 -8,-8s3.59,-8 8,-8 8,3.59 8,8 -3.59,8 -8,8z")

val Refresh: ImageVector get() = icon(
    "Refresh",
    "M17.65,6.35C16.2,4.9 14.21,4 12,4c-4.42,0 -7.99,3.58 -8,8s3.57,8 8,8 " +
        "c3.73,0 6.84,-2.55 7.73,-6h-2.08c-0.82,2.33 -3.04,4 -5.65,4 -3.31,0 -6,-2.69 -6,-6 " +
        "s2.69,-6 6,-6c1.66,0 3.14,0.69 4.22,1.78L13,11h7V4l-2.35,2.35z")

val Stop: ImageVector get() = icon(
    "Stop", "M6,6h12v12H6z")

val Terminal: ImageVector get() = icon(
    "Terminal",
    "M20,4H4c-1.1,0 -1.99,0.9 -1.99,2L2,18c0,1.1 0.9,2 2,2h16c1.1,0 2,-0.9 2,-2V6c0,-1.1 -0.9,-2 -2,-2z " +
        "M8.5,16.5L7,15l3,-3 -3,-3 1.5,-1.5L12,12l-3.5,4.5zM16,17h-6v-2h6v2z")

val Upload: ImageVector get() = icon(
    "Upload", "M9,16h6v-6h4l-7,-7 -7,7h4zM5,18h14v2H5z")
