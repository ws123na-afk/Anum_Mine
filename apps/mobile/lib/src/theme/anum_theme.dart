import 'package:flutter/material.dart';

abstract final class AnumSpacing {
  static const xxs = 4.0;
  static const xs = 8.0;
  static const sm = 12.0;
  static const md = 16.0;
  static const lg = 24.0;
  static const xl = 32.0;
  static const xxl = 40.0;
  static const xxxl = 48.0;
  static const huge = 64.0;
}

abstract final class AnumBreakpoints {
  static const compact = 600.0;
  static const expanded = 840.0;
}

abstract final class AnumTheme {
  static const _teal = Color(0xFF087F73);
  static const _charcoal = Color(0xFF192229);
  static const _lightSurface = Color(0xFFFFFFFF);
  static const _lightBackground = Color(0xFFF4F6F7);
  static const _darkSurface = Color(0xFF202B32);
  static const _darkBackground = Color(0xFF11181D);

  static ThemeData light() => _build(Brightness.light);
  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: _teal,
      brightness: brightness,
      surface: isDark ? _darkSurface : _lightSurface,
      error: const Color(0xFFB42318),
    );
    final base = ThemeData(
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: isDark ? _darkBackground : _lightBackground,
      useMaterial3: true,
    );
    return base.copyWith(
      textTheme: base.textTheme.apply(
        bodyColor: isDark ? Colors.white : _charcoal,
        displayColor: isDark ? Colors.white : _charcoal,
        fontFamily: 'Inter',
        fontFamilyFallback: const ['Noto Sans Arabic', 'Noto Naskh Arabic', 'Arial'],
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: scheme.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(8),
          side: BorderSide(color: scheme.outlineVariant),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surface,
        constraints: const BoxConstraints(minHeight: 52),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          minimumSize: const Size(48, 48),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(48, 48),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
      ),
      navigationBarTheme: const NavigationBarThemeData(height: 72),
      navigationRailTheme: const NavigationRailThemeData(minWidth: 80),
    );
  }
}
