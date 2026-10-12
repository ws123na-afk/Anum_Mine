import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Where the app's theme choice is kept: shared preferences in the app, a
/// map in tests.
abstract interface class ThemePreferenceStore {
  Future<String?> read();
  Future<void> write(String value);
}

class SharedPreferencesThemeStore implements ThemePreferenceStore {
  const SharedPreferencesThemeStore();
  static const key = 'anum.theme';
  @override
  Future<String?> read() async =>
      (await SharedPreferences.getInstance()).getString(key);
  @override
  Future<void> write(String value) async =>
      (await SharedPreferences.getInstance()).setString(key, value);
}

class MemoryThemeStore implements ThemePreferenceStore {
  MemoryThemeStore([this.value]);
  String? value;
  @override
  Future<String?> read() async => value;
  @override
  Future<void> write(String value) async => this.value = value;
}

/// Light, dark or the phone's setting (Profile and settings > Theme). Dark is
/// the default: the ANUM 360 look is designed dark first.
class ThemeController extends ChangeNotifier {
  ThemeController({ThemePreferenceStore? store})
      : _store = store ?? const SharedPreferencesThemeStore();

  final ThemePreferenceStore _store;
  ThemeMode mode = ThemeMode.dark;

  static ThemeMode? parse(String? value) => switch (value) {
        'light' => ThemeMode.light,
        'dark' => ThemeMode.dark,
        'system' => ThemeMode.system,
        _ => null,
      };

  Future<void> restore() async {
    try {
      final saved = parse(await _store.read());
      if (saved != null && saved != mode) {
        mode = saved;
        notifyListeners();
      }
    } on Object {
      // No stored choice (or storage unavailable): keep the default.
    }
  }

  Future<void> select(ThemeMode next) async {
    if (next == mode) return;
    mode = next;
    notifyListeners();
    try {
      await _store.write(next.name);
    } on Object {
      // The choice still applies for this session.
    }
  }
}
