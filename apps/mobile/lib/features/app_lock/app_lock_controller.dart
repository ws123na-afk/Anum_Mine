import 'dart:async';

import 'package:flutter/foundation.dart';

import 'device_auth.dart';

/// What the app lock covers.
enum AppLockScope {
  /// Finance, Books and approval decisions (the default).
  sensitive,

  /// Everything after sign-in.
  wholeApp,
}

/// A part of the app a gate stands in front of.
enum AppLockArea {
  /// Anything that is not Finance or Books.
  app,
  finance,
  books,
}

/// The app lock's settings on this phone. No secret is kept: the phone's own
/// screen lock does the checking.
@immutable
class AppLockSettings {
  const AppLockSettings({
    this.enabled = false,
    this.lockAfter = defaultLockAfter,
    this.scope = AppLockScope.sensitive,
  });

  /// "Lock after" choices: immediately, 1, 5 and 15 minutes.
  static const lockAfterChoices = [
    Duration.zero,
    Duration(minutes: 1),
    Duration(minutes: 5),
    Duration(minutes: 15),
  ];
  static const defaultLockAfter = Duration(minutes: 1);

  final bool enabled;

  /// How long the app may stay in the background before it locks again, and
  /// how long one unlock covers approval decisions.
  final Duration lockAfter;
  final AppLockScope scope;

  AppLockSettings copyWith(
          {bool? enabled, Duration? lockAfter, AppLockScope? scope}) =>
      AppLockSettings(
        enabled: enabled ?? this.enabled,
        lockAfter: lockAfter ?? this.lockAfter,
        scope: scope ?? this.scope,
      );

  Map<String, Object?> toJson() => {
        'enabled': enabled,
        'lock_after_seconds': lockAfter.inSeconds,
        'scope': scope.name,
      };

  /// Reads stored settings. Anything unexpected falls to the stricter value
  /// (an unknown delay locks immediately, an unknown scope is the whole app).
  factory AppLockSettings.fromJson(Map<String, Object?> json) {
    final seconds = json['lock_after_seconds'];
    final delay = seconds is int
        ? lockAfterChoices.firstWhere((d) => d.inSeconds == seconds,
            orElse: () => Duration.zero)
        : Duration.zero;
    return AppLockSettings(
      enabled: json['enabled'] == true,
      lockAfter: delay,
      scope: json['scope'] == AppLockScope.sensitive.name
          ? AppLockScope.sensitive
          : AppLockScope.wholeApp,
    );
  }

  @override
  bool operator ==(Object other) =>
      other is AppLockSettings &&
      other.enabled == enabled &&
      other.lockAfter == lockAfter &&
      other.scope == scope;

  @override
  int get hashCode => Object.hash(enabled, lockAfter, scope);
}

/// Where the settings live on the phone (secure storage in the app).
abstract interface class AppLockStore {
  /// The stored settings, or null when the lock was never turned on.
  Future<AppLockSettings?> read();
  Future<void> write(AppLockSettings settings);
  Future<void> clear();
}

class MemoryAppLockStore implements AppLockStore {
  MemoryAppLockStore([this.value]);
  AppLockSettings? value;

  @override
  Future<AppLockSettings?> read() async => value;

  @override
  Future<void> write(AppLockSettings settings) async => value = settings;

  @override
  Future<void> clear() async => value = null;
}

/// How a change in Settings ended.
enum AppLockChange {
  saved,

  /// The person did not pass the check, so nothing changed.
  notConfirmed,

  /// No screen lock, fingerprint or face is set up on the phone.
  notEnrolled,

  /// This phone cannot check.
  unsupported,

  /// The setting could not be stored.
  failed,
}

/// The optional app lock (docs/mobile.md#app-lock): the phone's biometrics
/// or screen lock in front of Finance, Books and approval decisions, or the
/// whole app. Locked on a cold start and after the app was in the background
/// for longer than "Lock after"; only a successful check unlocks.
class AppLockController extends ChangeNotifier {
  AppLockController({
    required this.authenticator,
    required this.store,
    DateTime Function()? now,
  }) : _now = now ?? DateTime.now;

  final DeviceAuthenticator authenticator;
  final AppLockStore store;
  final DateTime Function() _now;

  AppLockSettings settings = const AppLockSettings();

  /// False until the stored settings are read; gates stay closed meanwhile so
  /// nothing shows before the app knows whether it is locked.
  bool restored = false;

  /// Gated areas are closed until the next successful check.
  bool locked = false;

  /// A system prompt is open.
  bool authenticating = false;

  /// Why the last check did not unlock; null after a success or before any.
  DeviceAuthResult? lastFailure;

  DateTime? _unlockedAt;
  DateTime? _backgroundedAt;
  int _gatedScreens = 0;

  /// Counts resets, so a start-up read that finishes after a sign-out does
  /// not bring the old settings back.
  int _epoch = 0;
  bool _disposed = false;

  bool get enabled => settings.enabled;

  /// A Finance or Books screen is open somewhere in the app.
  bool get sensitiveScreenOpen => _gatedScreens > 0;

  /// Whether the lock stands in front of [area] at all.
  bool gates(AppLockArea area) =>
      enabled &&
      (settings.scope == AppLockScope.wholeApp || area != AppLockArea.app);

  /// Whether [area] must not be shown right now.
  bool blocks(AppLockArea area) => !restored || (locked && gates(area));

  /// Whether the whole window is covered: the whole app is locked, or a
  /// Finance or Books screen is open while those are locked (a dialog or a
  /// sheet above it is covered too).
  bool get coversWindow =>
      !restored ||
      (locked &&
          enabled &&
          (settings.scope == AppLockScope.wholeApp || sensitiveScreenOpen));

  /// Reads the stored settings at start-up. A lock that is on starts locked.
  /// Settings that cannot be read are treated as "on, immediately, whole
  /// app": a broken store must not open what the person locked.
  Future<void> restore() async {
    final epoch = _epoch;
    AppLockSettings read;
    try {
      read = await store.read() ?? const AppLockSettings();
    } on Object {
      read = const AppLockSettings(
          enabled: true,
          lockAfter: Duration.zero,
          scope: AppLockScope.wholeApp);
    }
    // Signed out meanwhile: the lock was reset and stays off.
    if (epoch != _epoch) return;
    settings = read;
    locked = settings.enabled;
    restored = true;
    _notify();
  }

  /// A Finance or Books screen came on (or went off) screen.
  void sensitiveScreenShown() {
    _gatedScreens++;
    scheduleMicrotask(_notify);
  }

  void sensitiveScreenGone() {
    if (_gatedScreens > 0) _gatedScreens--;
    scheduleMicrotask(_notify);
  }

  /// The app went to the background.
  void paused() {
    if (!enabled) return;
    _backgroundedAt ??= _now();
  }

  /// The app was away for "Lock after" or longer while a prompt was open;
  /// decided when the prompt ends.
  bool _lockAfterCheck = false;

  /// The app is in front again: lock when it was away for "Lock after" or
  /// longer (immediately locks on every return), or when the clock moved
  /// backwards meanwhile.
  void resumed() {
    if (!enabled) return;
    final since = _backgroundedAt;
    _backgroundedAt = null;
    if (since == null || locked) return;
    final away = _now().difference(since);
    final tooLong = away.isNegative || away >= settings.lockAfter;
    if (authenticating) {
      // The system prompt itself can pause the app (the screen-lock page on
      // older Android), so this is not yet the person leaving. A check that
      // passes is a fresh unlock; one that does not, after that long away,
      // locks ([_check]). Leaving the phone with a prompt open is no way in.
      if (tooLong) _lockAfterCheck = true;
      return;
    }
    if (tooLong) {
      locked = true;
      lastFailure = null;
      _notify();
    }
  }

  /// Asks the phone to check and unlocks on success.
  Future<DeviceAuthResult> unlock({required String reason}) async {
    final result = await _check(reason);
    if (result == DeviceAuthResult.success) {
      locked = false;
      _notify();
    }
    return result;
  }

  /// Before an approval decision: true when the lock is off or the last
  /// successful check is younger than "Lock after"; otherwise asks again
  /// (always, when "Lock after" is immediately).
  Future<bool> confirmSensitiveAction({required String reason}) async {
    if (!restored) return false;
    if (!enabled) return true;
    final last = _unlockedAt;
    if (!locked && last != null) {
      final age = _now().difference(last);
      if (!age.isNegative && age < settings.lockAfter) return true;
    }
    final result = await _check(reason);
    if (result == DeviceAuthResult.success && locked) {
      locked = false;
      _notify();
    }
    return result == DeviceAuthResult.success;
  }

  Future<DeviceAuthResult> _check(String reason) async {
    if (authenticating) return DeviceAuthResult.unavailable;
    authenticating = true;
    _notify();
    DeviceAuthResult result;
    try {
      result = await authenticator.authenticate(reason: reason);
    } on Object {
      result = DeviceAuthResult.unavailable;
    }
    authenticating = false;
    if (result == DeviceAuthResult.success) {
      // A fresh unlock: time away during the prompt does not count.
      _unlockedAt = _now();
      _backgroundedAt = null;
      _lockAfterCheck = false;
      lastFailure = null;
    } else {
      lastFailure = result;
      if (_lockAfterCheck && enabled) locked = true;
      _lockAfterCheck = false;
    }
    _notify();
    return result;
  }

  /// Turns the lock on after one successful check. Without a screen lock on
  /// the phone it stays off and says so.
  Future<AppLockChange> enable({required String reason}) async {
    DeviceAuthSupport support;
    try {
      support = await authenticator.support();
    } on Object {
      support = DeviceAuthSupport.unsupported;
    }
    if (support == DeviceAuthSupport.unsupported) {
      return AppLockChange.unsupported;
    }
    if (support == DeviceAuthSupport.notEnrolled) {
      return AppLockChange.notEnrolled;
    }
    final result = await _check(reason);
    if (result == DeviceAuthResult.notEnrolled) {
      return AppLockChange.notEnrolled;
    }
    if (result != DeviceAuthResult.success) return AppLockChange.notConfirmed;
    return _save(settings.copyWith(enabled: true), locked: false);
  }

  /// Turns the lock off: only the person who can unlock may do that.
  Future<AppLockChange> disable({required String reason}) =>
      _change(reason, settings.copyWith(enabled: false));

  Future<AppLockChange> setLockAfter(Duration value,
          {required String reason}) =>
      _change(reason, settings.copyWith(lockAfter: value));

  Future<AppLockChange> setScope(AppLockScope value,
          {required String reason}) =>
      _change(reason, settings.copyWith(scope: value));

  Future<AppLockChange> _change(String reason, AppLockSettings next) async {
    if (!enabled) return _save(next, locked: false);
    if (next == settings) return AppLockChange.saved;
    final result = await _check(reason);
    if (result != DeviceAuthResult.success) return AppLockChange.notConfirmed;
    return _save(next, locked: false);
  }

  Future<AppLockChange> _save(AppLockSettings next,
      {required bool locked}) async {
    try {
      if (next.enabled) {
        await store.write(next);
      } else {
        // Off keeps nothing behind, like a phone that never had it on.
        await store.clear();
      }
    } on Object {
      return AppLockChange.failed;
    }
    settings = next.enabled ? next : const AppLockSettings();
    this.locked = locked;
    _notify();
    return AppLockChange.saved;
  }

  /// Sign-out: the lock belongs to the person who set it, so it is turned off
  /// and forgotten with the session.
  void reset() {
    if (authenticating) unawaited(authenticator.cancel().catchError((_) {}));
    _epoch++;
    settings = const AppLockSettings();
    locked = false;
    lastFailure = null;
    _unlockedAt = null;
    _backgroundedAt = null;
    _lockAfterCheck = false;
    restored = true;
    unawaited(store.clear().catchError((Object _) {}));
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// Whether a request is an approval decision, which the app lock asks about
/// again when the last unlock is older than "Lock after": approving or
/// rejecting an approval, a change order, a trade order, a bill, an
/// adjustment, a guardian change, or deciding a salary month.
bool isApprovalDecision(String method, String path) {
  if (method.toUpperCase() != 'POST') return false;
  final clean = path.split('?').first;
  return RegExp(r'/(approvals|change-orders|intents|bills|adjustments|changes)'
              r'/[^/]+/(approve|reject)$')
          .hasMatch(clean) ||
      RegExp(r'/salary/months/[^/]+/decision$').hasMatch(clean);
}
