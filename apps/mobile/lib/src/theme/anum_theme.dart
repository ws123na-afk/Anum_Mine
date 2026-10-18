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

/// Corner radii: quiet, slightly rounded cards and controls, pill chips.
abstract final class AnumRadius {
  static const sm = 8.0;
  static const md = 10.0;
  static const lg = 14.0;
  static const xl = 20.0;
  static const pill = 999.0;
}

/// Motion is short and only marks a change of state (press, select, expand):
/// 150 to 200 ms. Everything honours reduced motion
/// (`MediaQuery.disableAnimations`): [of] returns zero then.
abstract final class AnumMotion {
  static const quick = Duration(milliseconds: 150);
  static const standard = Duration(milliseconds: 200);
  static const page = Duration(milliseconds: 200);
  static const curve = Curves.easeOutCubic;

  /// One full cycle of the orb's waveform (the orb is the brand object and
  /// stops when motion is reduced).
  static const orbCycle = Duration(milliseconds: 2400);

  /// [duration], or zero when the person asked for reduced motion.
  static Duration of(BuildContext context, [Duration duration = standard]) =>
      (MediaQuery.maybeDisableAnimationsOf(context) ?? false)
          ? Duration.zero
          : duration;
}

/// The ANUM palette (docs/design/anum360.md#theme): a calm, premium
/// enterprise look. A restrained neutral base, one brand accent (ANUM iris)
/// for primary actions, links, focus and selection, semantic colours only
/// for real status, hairline borders ([line]) and soft depth ([shadow]).
/// No gradient text; the brand gradient ([brandStart] to [brandEnd]) is kept
/// for the logo mark and the orb only.
///
/// Field names are kept from the first palette so every screen inherits the
/// new look: [sky], [violet] and [rose] are all the accent as text and icons
/// (AA on every surface), and [accentStart] equals [accentEnd] (the accent
/// as a fill under white text), so [accentGradient] paints a solid accent.
@immutable
class AnumPalette extends ThemeExtension<AnumPalette> {
  const AnumPalette({
    required this.background,
    required this.surface1,
    required this.surface2,
    required this.surface3,
    required this.line,
    required this.lineStrong,
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
    required this.accentSoft,
    this.accentStart = const Color(0xFF5145CD),
    this.accentEnd = const Color(0xFF5145CD),
    this.onAccent = const Color(0xFFFFFFFF),
    this.glow = const Color(0xFF5145CD),
    this.glass = const Color(0xFAFFFFFF),
    this.brandStart = const Color(0xFF4F8BFF),
    this.brandMiddle = const Color(0xFF6A5CFF),
    this.brandEnd = const Color(0xFF9B6BFF),
  });

  /// Page, then raised surfaces (cards, sheets) and the well or hover step.
  final Color background, surface1, surface2, surface3;

  /// Hairline borders and dividers; [lineStrong] for inputs and outlines.
  final Color line, lineStrong;

  /// Inner highlight of a raised surface (transparent in this look).
  final Color edge;

  /// Text: primary, secondary (7:1) and tertiary (4.5:1 on every surface).
  final Color text, muted, faint;

  /// The accent as text and icons ([sky], [violet] and [rose] are the same).
  final Color sky, violet, rose;

  /// Status only, always with a text label.
  final Color ok, warn, stop;

  /// Soft ambient shadow under raised surfaces.
  final Color shadow;

  /// The accent's tint: selected rows, chips, the navigation indicator.
  final Color accentSoft;

  /// The accent as a fill; [onAccent] on it keeps at least 4.5:1.
  final Color accentStart, accentEnd, onAccent;

  /// The orb's glow.
  final Color glow;

  /// Opaque chrome (bottom navigation, drawer).
  final Color glass;

  /// The ANUM mark and orb gradient (identity only, never text or buttons).
  final Color brandStart, brandMiddle, brandEnd;

  /// Kept for existing callers: a solid accent (both ends are the same).
  LinearGradient get accentGradient => LinearGradient(
      begin: AlignmentDirectional.centerStart,
      end: AlignmentDirectional.centerEnd,
      colors: [accentStart, accentEnd]);

  static const dark = AnumPalette(
    background: Color(0xFF0B0B0F),
    surface1: Color(0xFF131318),
    surface2: Color(0xFF17171D),
    surface3: Color(0xFF1E1E25),
    line: Color(0xFF2A2A33),
    lineStrong: Color(0xFF3A3A45),
    edge: Color(0x00000000),
    text: Color(0xFFEDEDF0),
    muted: Color(0xFFA1A1AA),
    faint: Color(0xFF8B8B95),
    sky: Color(0xFFA9A4FF),
    violet: Color(0xFFA9A4FF),
    rose: Color(0xFFA9A4FF),
    ok: Color(0xFF6EE7A0),
    warn: Color(0xFFFBBF24),
    stop: Color(0xFFF87171),
    shadow: Color(0x66000000),
    accentSoft: Color(0xFF24223D),
    accentStart: Color(0xFF5B50E0),
    accentEnd: Color(0xFF5B50E0),
    glow: Color(0xFF5B50E0),
    glass: Color(0xFA131318),
  );

  static const light = AnumPalette(
    background: Color(0xFFF7F7F8),
    surface1: Color(0xFFFFFFFF),
    surface2: Color(0xFFFFFFFF),
    surface3: Color(0xFFF1F1F3),
    line: Color(0xFFE4E4E7),
    lineStrong: Color(0xFFD4D4D8),
    edge: Color(0x00FFFFFF),
    text: Color(0xFF18181B),
    muted: Color(0xFF52525B),
    faint: Color(0xFF6B6B74),
    sky: Color(0xFF5145CD),
    violet: Color(0xFF5145CD),
    rose: Color(0xFF5145CD),
    ok: Color(0xFF166534),
    warn: Color(0xFF92400E),
    stop: Color(0xFFB42335),
    shadow: Color(0x1418181B),
    accentSoft: Color(0xFFEEEDFB),
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
  /// The type family and its Arabic fallbacks. Component styles (buttons,
  /// chips, snack bars, navigation labels, field labels) replace the text
  /// theme instead of merging with it, so they name the family themselves
  /// through [_ui]; otherwise they would fall back to the engine default.
  static const fontFamily = 'Inter';
  static const fontFamilyFallback = [
    'Noto Sans Arabic',
    'Noto Naskh Arabic',
    'Arial'
  ];

  static TextStyle _ui(
          {Color? color, FontWeight? fontWeight, double? fontSize}) =>
      TextStyle(
          fontFamily: fontFamily,
          fontFamilyFallback: fontFamilyFallback,
          color: color,
          fontWeight: fontWeight,
          fontSize: fontSize);

  static ThemeData light() => _build(Brightness.light);
  static ThemeData dark() => _build(Brightness.dark);

  /// The type scale: Inter (Noto Sans Arabic for Arabic), tight headings,
  /// calm body text. Sizes follow Material 3 so text scaling behaves.
  static TextTheme _textTheme(TextTheme base, AnumPalette p) {
    // Material 3 sizes and line heights are kept (layouts and 200% text
    // depend on them); headings get the tighter tracking and weights of the
    // ANUM look, secondary styles the muted colour.
    TextStyle? heading(TextStyle? s, FontWeight weight, double spacing) =>
        s?.copyWith(fontWeight: weight, letterSpacing: spacing);
    return base
        .copyWith(
          displayLarge: heading(base.displayLarge, FontWeight.w700, -1),
          displayMedium: heading(base.displayMedium, FontWeight.w700, -0.8),
          displaySmall: heading(base.displaySmall, FontWeight.w700, -0.6),
          headlineLarge: heading(base.headlineLarge, FontWeight.w700, -0.5),
          headlineMedium: heading(base.headlineMedium, FontWeight.w600, -0.4),
          headlineSmall: heading(base.headlineSmall, FontWeight.w600, -0.3),
          titleLarge: heading(base.titleLarge, FontWeight.w600, -0.2),
          titleMedium: heading(base.titleMedium, FontWeight.w600, -0.1),
          titleSmall: heading(base.titleSmall, FontWeight.w600, 0),
        )
        .apply(
          bodyColor: p.text,
          displayColor: p.text,
          fontFamily: fontFamily,
          fontFamilyFallback: fontFamilyFallback,
        )
        .copyWith(
          bodySmall: base.bodySmall?.copyWith(
              color: p.muted,
              fontFamily: fontFamily,
              fontFamilyFallback: fontFamilyFallback),
          labelSmall: base.labelSmall?.copyWith(
              color: p.muted,
              fontFamily: fontFamily,
              fontFamilyFallback: fontFamilyFallback),
        );
  }

  static ThemeData _build(Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    final p = isDark ? AnumPalette.dark : AnumPalette.light;
    final onTone = isDark ? p.background : p.onAccent;
    final scheme = ColorScheme(
      brightness: brightness,
      primary: p.accentStart,
      onPrimary: p.onAccent,
      primaryContainer: p.accentSoft,
      onPrimaryContainer: p.violet,
      secondary: p.violet,
      onSecondary: onTone,
      secondaryContainer: p.accentSoft,
      onSecondaryContainer: p.text,
      tertiary: p.violet,
      onTertiary: onTone,
      error: p.stop,
      onError: onTone,
      errorContainer:
          Color.alphaBlend(p.stop.withValues(alpha: 0.12), p.surface1),
      onErrorContainer: p.stop,
      surface: p.surface1,
      onSurface: p.text,
      onSurfaceVariant: p.muted,
      surfaceDim: p.background,
      surfaceBright: p.surface2,
      surfaceContainerLowest: p.background,
      surfaceContainerLow: p.surface1,
      surfaceContainer: p.surface2,
      surfaceContainerHigh: p.surface3,
      surfaceContainerHighest: p.surface3,
      outline: p.lineStrong,
      outlineVariant: p.line,
      shadow: p.shadow,
      scrim: const Color(0x99000000),
      inverseSurface: p.text,
      onInverseSurface: p.background,
      inversePrimary: isDark ? p.accentStart : p.accentSoft,
      surfaceTint: Colors.transparent,
    );
    final base = ThemeData(
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: p.background,
      canvasColor: p.background,
      useMaterial3: true,
      extensions: [p],
    );
    final text = _textTheme(base.textTheme, p);
    final control = BorderRadius.circular(AnumRadius.md);
    final card = BorderRadius.circular(AnumRadius.lg);
    OutlineInputBorder field(Color color, [double width = 1]) =>
        OutlineInputBorder(
            borderRadius: control,
            borderSide: BorderSide(color: color, width: width));
    bool selected(Set<WidgetState> s) => s.contains(WidgetState.selected);
    return base.copyWith(
      textTheme: text,
      primaryTextTheme: text,
      iconTheme: IconThemeData(color: p.muted, size: 22),
      appBarTheme: AppBarTheme(
        backgroundColor: p.background,
        surfaceTintColor: Colors.transparent,
        foregroundColor: p.text,
        elevation: 0,
        scrolledUnderElevation: 0,
        titleTextStyle:
            _ui(color: p.text, fontWeight: FontWeight.w600, fontSize: 17),
        iconTheme: IconThemeData(color: p.text),
        actionsIconTheme: IconThemeData(color: p.muted),
        shape: Border(bottom: BorderSide(color: p.line)),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: p.surface1,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
            borderRadius: card, side: BorderSide(color: p.line)),
      ),
      dividerTheme: DividerThemeData(color: p.line, space: 1, thickness: 1),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: p.surface1,
        constraints: const BoxConstraints(minHeight: 48),
        border: field(p.lineStrong),
        enabledBorder: field(p.lineStrong),
        disabledBorder: field(p.line),
        focusedBorder: field(p.accentStart, 1.5),
        errorBorder: field(p.stop),
        focusedErrorBorder: field(p.stop, 1.5),
        labelStyle: _ui(color: p.muted),
        floatingLabelStyle: _ui(color: p.violet),
        hintStyle: _ui(color: p.faint),
        helperStyle: _ui(color: p.muted, fontSize: 12.5),
        errorStyle: _ui(color: p.stop, fontSize: 12.5),
        prefixIconColor: p.muted,
        suffixIconColor: p.muted,
      ),
      textSelectionTheme: TextSelectionThemeData(
        cursorColor: p.accentStart,
        selectionColor: p.accentStart.withValues(alpha: 0.28),
        selectionHandleColor: p.accentStart,
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(48, 48),
          backgroundColor: p.accentStart,
          foregroundColor: p.onAccent,
          disabledBackgroundColor: p.surface3,
          disabledForegroundColor: p.faint,
          elevation: 0,
          padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.lg),
          shape: RoundedRectangleBorder(borderRadius: control),
          textStyle: _ui(fontWeight: FontWeight.w600, fontSize: 14.5),
          animationDuration: AnumMotion.quick,
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          minimumSize: const Size(48, 48),
          backgroundColor: p.surface1,
          foregroundColor: p.text,
          elevation: 0,
          shadowColor: Colors.transparent,
          side: BorderSide(color: p.lineStrong),
          shape: RoundedRectangleBorder(borderRadius: control),
          textStyle: _ui(fontWeight: FontWeight.w600, fontSize: 14.5),
          animationDuration: AnumMotion.quick,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(48, 48),
          foregroundColor: p.text,
          backgroundColor: p.surface1,
          side: BorderSide(color: p.lineStrong),
          padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.md),
          shape: RoundedRectangleBorder(borderRadius: control),
          textStyle: _ui(fontWeight: FontWeight.w600, fontSize: 14.5),
          animationDuration: AnumMotion.quick,
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          minimumSize: const Size(48, 48),
          foregroundColor: p.violet,
          shape: RoundedRectangleBorder(borderRadius: control),
          textStyle: _ui(fontWeight: FontWeight.w600, fontSize: 14.5),
          animationDuration: AnumMotion.quick,
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: p.muted,
        ),
      ),
      segmentedButtonTheme: SegmentedButtonThemeData(
        style: SegmentedButton.styleFrom(
          minimumSize: const Size(48, 44),
          backgroundColor: p.surface1,
          foregroundColor: p.muted,
          selectedBackgroundColor: p.accentSoft,
          selectedForegroundColor: p.violet,
          side: BorderSide(color: p.lineStrong),
          shape: RoundedRectangleBorder(borderRadius: control),
          textStyle: _ui(fontWeight: FontWeight.w600, fontSize: 13.5),
        ),
      ),
      chipTheme: base.chipTheme.copyWith(
        backgroundColor: p.surface1,
        selectedColor: p.accentSoft,
        disabledColor: p.surface3,
        checkmarkColor: p.violet,
        side: BorderSide(color: p.line),
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AnumRadius.pill)),
        labelStyle:
            _ui(color: p.text, fontWeight: FontWeight.w500, fontSize: 13.5),
        secondaryLabelStyle:
            _ui(color: p.violet, fontWeight: FontWeight.w600, fontSize: 13.5),
        iconTheme: IconThemeData(color: p.muted, size: 18),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: p.muted,
        textColor: p.text,
        selectedColor: p.violet,
        // No theme-wide selectedTileColor: a tile colour makes every
        // ListTile need a Material directly behind it. Lists that select
        // (the menu drawer) set p.accentSoft themselves.
        minVerticalPadding: 12,
        shape: RoundedRectangleBorder(borderRadius: control),
        titleTextStyle:
            _ui(color: p.text, fontWeight: FontWeight.w500, fontSize: 15),
        subtitleTextStyle: _ui(color: p.muted, fontSize: 13),
      ),
      tabBarTheme: TabBarThemeData(
        labelColor: p.text,
        unselectedLabelColor: p.muted,
        indicatorColor: p.accentStart,
        indicatorSize: TabBarIndicatorSize.label,
        dividerColor: p.line,
        labelStyle: _ui(fontWeight: FontWeight.w600, fontSize: 14),
        unselectedLabelStyle: _ui(fontWeight: FontWeight.w500, fontSize: 14),
        indicator: UnderlineTabIndicator(
            borderSide: BorderSide(color: p.accentStart, width: 2),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(2))),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: p.surface1,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        dragHandleColor: p.lineStrong,
        shape: RoundedRectangleBorder(
            borderRadius: const BorderRadius.vertical(
                top: Radius.circular(AnumRadius.xl)),
            side: BorderSide(color: p.line)),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: p.surface1,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        titleTextStyle:
            _ui(color: p.text, fontWeight: FontWeight.w600, fontSize: 18),
        contentTextStyle: _ui(color: p.muted, fontSize: 14.5),
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AnumRadius.xl),
            side: BorderSide(color: p.line)),
      ),
      popupMenuTheme: PopupMenuThemeData(
        color: p.surface1,
        surfaceTintColor: Colors.transparent,
        elevation: 6,
        shadowColor: p.shadow,
        textStyle: _ui(color: p.text, fontSize: 14.5),
        shape: RoundedRectangleBorder(
            borderRadius: control, side: BorderSide(color: p.line)),
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
            color: p.text, borderRadius: BorderRadius.circular(AnumRadius.sm)),
        textStyle: _ui(color: p.background, fontSize: 12.5),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: p.text,
        contentTextStyle: _ui(color: p.background, fontSize: 14),
        actionTextColor: isDark ? p.accentStart : p.accentSoft,
        behavior: SnackBarBehavior.floating,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: control),
      ),
      navigationBarTheme: NavigationBarThemeData(
        height: 68,
        elevation: 0,
        backgroundColor: p.glass,
        surfaceTintColor: Colors.transparent,
        indicatorColor: p.accentSoft,
        indicatorShape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AnumRadius.pill)),
        iconTheme: WidgetStateProperty.resolveWith((s) =>
            IconThemeData(color: selected(s) ? p.violet : p.muted, size: 22)),
        labelTextStyle: WidgetStateProperty.resolveWith((s) => _ui(
            fontSize: 12,
            fontWeight: selected(s) ? FontWeight.w600 : FontWeight.w500,
            color: selected(s) ? p.text : p.muted)),
      ),
      navigationRailTheme: NavigationRailThemeData(
        minWidth: 80,
        backgroundColor: Colors.transparent,
        indicatorColor: p.accentSoft,
        selectedIconTheme: IconThemeData(color: p.violet),
        unselectedIconTheme: IconThemeData(color: p.muted),
        selectedLabelTextStyle:
            _ui(color: p.text, fontWeight: FontWeight.w600, fontSize: 12.5),
        unselectedLabelTextStyle: _ui(color: p.muted, fontSize: 12.5),
      ),
      navigationDrawerTheme: NavigationDrawerThemeData(
        backgroundColor: p.surface1,
        surfaceTintColor: Colors.transparent,
        indicatorColor: p.accentSoft,
      ),
      drawerTheme: DrawerThemeData(
        backgroundColor: p.surface1,
        surfaceTintColor: Colors.transparent,
      ),
      badgeTheme:
          BadgeThemeData(backgroundColor: p.accentStart, textColor: p.onAccent),
      progressIndicatorTheme: ProgressIndicatorThemeData(
        color: p.accentStart,
        linearTrackColor: p.surface3,
        circularTrackColor: Colors.transparent,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith(
            (s) => selected(s) ? p.onAccent : p.muted),
        trackColor: WidgetStateProperty.resolveWith(
            (s) => selected(s) ? p.accentStart : p.surface3),
        trackOutlineColor: WidgetStateProperty.resolveWith(
            (s) => selected(s) ? Colors.transparent : p.lineStrong),
      ),
      checkboxTheme: CheckboxThemeData(
        fillColor: WidgetStateProperty.resolveWith(
            (s) => selected(s) ? p.accentStart : null),
        checkColor: WidgetStatePropertyAll(p.onAccent),
        side: BorderSide(color: p.lineStrong, width: 1.5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
      ),
      radioTheme: RadioThemeData(
        fillColor: WidgetStateProperty.resolveWith(
            (s) => selected(s) ? p.accentStart : p.lineStrong),
      ),
      sliderTheme: SliderThemeData(
        activeTrackColor: p.accentStart,
        inactiveTrackColor: p.surface3,
        thumbColor: p.accentStart,
      ),
      expansionTileTheme: ExpansionTileThemeData(
        iconColor: p.muted,
        collapsedIconColor: p.muted,
        textColor: p.text,
        collapsedTextColor: p.text,
        shape: const Border(),
        collapsedShape: const Border(),
      ),
    );
  }
}
