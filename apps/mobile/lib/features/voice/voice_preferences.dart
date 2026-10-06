import 'package:shared_preferences/shared_preferences.dart';

/// Small key/value store for voice settings (name, wake, language, mute).
abstract interface class VoicePreferences {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
}

class SharedVoicePreferences implements VoicePreferences {
  Future<SharedPreferences>? _prefs;
  Future<SharedPreferences> get _store =>
      _prefs ??= SharedPreferences.getInstance();

  @override
  Future<String?> read(String key) async {
    try {
      return (await _store).getString(key);
    } on Object {
      return null;
    }
  }

  @override
  Future<void> write(String key, String value) async {
    try {
      await (await _store).setString(key, value);
    } on Object {
      // Preferences are a convenience; the session keeps working without them.
    }
  }
}

class MemoryVoicePreferences implements VoicePreferences {
  MemoryVoicePreferences([Map<String, String>? values]) : values = values ?? {};
  final Map<String, String> values;

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async => values[key] = value;
}
