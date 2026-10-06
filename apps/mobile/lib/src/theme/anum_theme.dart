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

/// Depth palette: "midnight + aurora". Surfaces get lighter as they rise
/// (dark mode) and every raised surface carries a lit top edge.
@immutable
class AnumPalette extends ThemeExtension<AnumPalette> {
  const AnumPalette({
    required this.background,
    required this.surface1,
    required this.surface2,
    required this.surface3,
    required this.line,
    required this.edge,
    required this.text,
    required this.muted,
    required this.faint,
    required this.sky,
    required this.violet,
    required this.rose,
    required this.ok,
    required this.warn,
    required this.stop,
    required this.shadow,
  });

  final Color background, surface1, surface2, surface3, line, edge;
  final Color text, muted, faint, sky, violet, rose, ok, warn, stop, shadow;

  static const dark = AnumPalette(
    background: Color(0xFF0B1020),
    surface1: Color(0xFF111829),
    surface2: Color(0xFF172036),
    surface3: Color(0xFF1E2944),
    line: Color(0x17FFFFFF),
    edge: Color(0x1AFFFFFF),
    text: Color(0xFFEEF1F8),
    muted: Color(0xFFA3ABC2),
    faint: Color(0xFF8790A8),
    sky: Color(0xFF7DD3FC),
    violet: Color(0xFFA78BFA),
    rose: Color(0xFFF9A8D4),
    ok: Color(0xFF86EFAC),
    warn: Color(0xFFFCD38D),
    stop: Color(0xFFFDA4AF),
    shadow: Color(0x99020617),
  );

  static const light = AnumPalette(
    background: Color(0xFFF4F6FB),
    surface1: Color(0xFFFFFFFF),
    surface2: Color(0xFFFBFCFF),
    surface3: Color(0xFFFFFFFF),
    line: Color(0x1A141E3C),
    edge: Color(0xE6FFFFFF),
    text: Color(0xFF0E1424),
    muted: Color(0xFF45506B),
    faint: Color(0xFF5E6884),
    sky: Color(0xFF1F6FD1),
    violet: Color(0xFF5B4BE0),
    rose: Color(0xFFD23A86),
    ok: Color(0xFF14915E),
    warn: Color(0xFFB76E00),
    stop: Color(0xFFD12F40),
    shadow: Color(0x24101830),
  );

  @override
  AnumPalette copyWith() => this;

  @override
  AnumPalette lerp(ThemeExtension<AnumPalette>? other, double t) =>
      other is AnumPalette && t >= 0.5 ? other : this;
}

extension AnumPaletteContext on BuildContext {
  AnumPalette get palette =>
      Theme.of(this).extension<AnumPalette>() ?? AnumPalette.dark;
}

abstract final class AnumTheme {
  static ThemeData light() => _build(Brightness.light);
  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    final p = isDark ? AnumPalette.dark : AnumPalette.light;
    final scheme = ColorScheme.fromSeed(
      seedColor: p.violet,
      brightness: brightness,
      primary: p.violet,
      secondary: p.sky,
      tertiary: p.rose,
      surface: p.surface1,
      onSurface: p.text,
      error: p.stop,
      outline: p.faint,
      outlineVariant: p.line,
    );
    final base = ThemeData(
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: p.background,
      canvasColor: p.background,
      useMaterial3: true,
      extensions: [p],
    );
    final radius = BorderRadius.circular(16);
    return base.copyWith(
      textTheme: base.textTheme.apply(
        bodyColor: p.text,
        displayColor: p.text,
        fontFamily: 'Inter',
        fontFamilyFallback: const [
          'Noto Sans Arabic',
          'Noto Naskh Arabic',
          'Arial'
        ],
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        foregroundColor: p.text,
        elevation: 0,
        scrolledUnderElevation: 0,
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: p.surface2,
        shape: RoundedRectangleBorder(
            borderRadius: radius, side: BorderSide(color: p.line)),
      ),
      dividerTheme: DividerThemeData(color: p.line, space: 1),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: isDark ? const Color(0x73020617) : p.surface1,
        constraints: const BoxConstraints(minHeight: 52),
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide(color: p.line)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide(color: p.line)),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide(color: p.sky, width: 1.6)),
        labelStyle: TextStyle(color: p.muted),
        hintStyle: TextStyle(color: p.faint),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(48, 48),
          backgroundColor: p.violet,
          foregroundColor: Colors.white,
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          minimumSize: const Size(48, 48),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(48, 48),
          foregroundColor: p.text,
          side: BorderSide(color: p.line),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
            minimumSize: const Size(48, 48), foregroundColor: p.sky),
      ),
      chipTheme: base.chipTheme.copyWith(
        backgroundColor: p.surface2,
        selectedColor: p.violet.withValues(alpha: 0.22),
        side: BorderSide(color: p.line),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(999)),
        labelStyle: TextStyle(color: p.text, fontWeight: FontWeight.w600),
      ),
      listTileTheme: ListTileThemeData(
          iconColor: p.muted, textColor: p.text, minVerticalPadding: 12),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: p.surface1,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: p.surface2,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(22)),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: p.surface3,
        contentTextStyle: TextStyle(color: p.text),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      navigationBarTheme: NavigationBarThemeData(
        height: 72,
        backgroundColor:
            isDark ? const Color(0xE6111829) : const Color(0xF2FFFFFF),
        surfaceTintColor: Colors.transparent,
        indicatorColor: p.violet.withValues(alpha: 0.22),
        labelTextStyle: WidgetStatePropertyAll(TextStyle(
            fontSize: 12, fontWeight: FontWeight.w600, color: p.text)),
      ),
      navigationRailTheme: NavigationRailThemeData(
        minWidth: 80,
        backgroundColor: Colors.transparent,
        indicatorColor: p.violet.withValues(alpha: 0.22),
        selectedLabelTextStyle:
            TextStyle(color: p.text, fontWeight: FontWeight.w700),
        unselectedLabelTextStyle: TextStyle(color: p.muted),
      ),
      progressIndicatorTheme: ProgressIndicatorThemeData(color: p.sky),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? Colors.white : p.muted),
        trackColor: WidgetStateProperty.resolveWith(
            (s) => s.contains(WidgetState.selected) ? p.violet : p.surface3),
      ),
    );
  }
}
