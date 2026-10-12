import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Whether this device has seen the onboarding pages; kept in shared
/// preferences in the app, in memory in tests.
abstract interface class IntroStore {
  Future<bool> seen();
  Future<void> markSeen();
}

class SharedPreferencesIntroStore implements IntroStore {
  const SharedPreferencesIntroStore();
  static const key = 'anum.onboarding.seen';
  @override
  Future<bool> seen() async =>
      (await SharedPreferences.getInstance()).getBool(key) ?? false;
  @override
  Future<void> markSeen() async =>
      (await SharedPreferences.getInstance()).setBool(key, true);
}

class MemoryIntroStore implements IntroStore {
  MemoryIntroStore({this.value = false});
  bool value;
  @override
  Future<bool> seen() async => value;
  @override
  Future<void> markSeen() async => value = true;
}

/// Shows the onboarding pages once per device, before the first sign-in.
/// Until [restore] finishes, [show] is false, so a slow read never flashes
/// the pages at someone who has seen them.
class IntroController extends ChangeNotifier {
  IntroController({IntroStore? store})
      : _store = store ?? const SharedPreferencesIntroStore();

  final IntroStore _store;
  bool _seen = true;
  bool _restored = false;

  bool get show => _restored && !_seen;

  Future<void> restore() async {
    try {
      _seen = await _store.seen();
    } on Object {
      _seen = true;
    }
    _restored = true;
    notifyListeners();
  }

  /// "Get Started" asks for Create account; Sign in and Skip do not.
  bool _register = false;

  /// Whether the sign-in screen should open on Create account, once.
  bool takeRegistration() {
    final value = _register;
    _register = false;
    return value;
  }

  Future<void> finish({bool register = false}) async {
    _register = register;
    _seen = true;
    notifyListeners();
    try {
      await _store.markSeen();
    } on Object {
      // Shown again next launch at worst.
    }
  }
}
