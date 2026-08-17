package dev.anum.android.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext

// Brand-neutral placeholder palette (see apps/web/src/styles.css's design
// tokens for the reference light/dark pair this mirrors) - swap for real
// brand colors when a design system lands here.
private val AnumLightColors = lightColorScheme(primary = androidx.compose.ui.graphics.Color(0xFF4F46E5))
private val AnumDarkColors = darkColorScheme(primary = androidx.compose.ui.graphics.Color(0xFF818CF8))

@Composable
fun AnumTheme(content: @Composable () -> Unit) {
    val darkTheme = isSystemInDarkTheme()
    val context = LocalContext.current
    val colorScheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        darkTheme -> AnumDarkColors
        else -> AnumLightColors
    }
    MaterialTheme(colorScheme = colorScheme, content = content)
}
