/// The type scale: the only place a raw font size may be written
/// (`test/type_scale_test.dart`). Sizes are logical pixels and scale with the
/// OS text size through `MediaQuery.textScaler`, so a layout that uses them
/// must stay content-sized (no fixed heights) to survive 200% text.
///
/// The whole steps mirror the web scale (`apps/web/src/typography.css`,
/// docs/design/visual-identity.md#type). The `*Plus` steps are the half-pixel
/// sizes the mobile screens already used; they keep their exact values so
/// moving to tokens changes nothing on screen.
abstract final class AnumType {
  /// 10: counters and the smallest badge text.
  static const double micro = 10;

  /// 11: badges and uppercase micro labels.
  static const double xxs = 11;
  static const double xxsPlus = 11.5;

  /// 12: captions, timestamps.
  static const double xs = 12;
  static const double xsPlus = 12.5;

  /// 13: labels, secondary text, dense rows.
  static const double sm = 13;
  static const double smPlus = 13.5;

  /// 14: default UI text and body.
  static const double md = 14;
  static const double mdPlus = 14.5;

  /// 15: comfortable body, card titles.
  static const double base = 15;
  static const double basePlus = 15.5;

  /// 16: lead text and inputs.
  static const double lg = 16;
  static const double lgPlus = 17;

  /// 18: section titles.
  static const double xl = 18;
  static const double xlPlus = 19;

  /// 20 and up: panel titles, figures, display.
  static const double x2l = 20;
  static const double x2lPlus = 21;
  static const double x3l = 22;
  static const double x4l = 24;

  /// 26: legacy in-between size, kept so nothing moves on screen.
  static const double x4lPlus = 26;
  static const double x5l = 28;

  /// 30: legacy in-between size.
  static const double x5lPlus = 30;
  static const double x6l = 32;
}
