/// The phone's own "is it you?" check (fingerprint, face, or the screen lock's
/// PIN, pattern or passcode), behind an interface so the app lock is tested
/// with a fake. The plugin implementation is
/// `lib/src/infrastructure/local_auth_authenticator.dart`, the only file that
/// imports `local_auth` (docs/mobile.md#app-lock).
library;

/// Whether this phone can check.
enum DeviceAuthSupport {
  /// A screen lock is set (with or without biometrics).
  available,

  /// The phone can check, but no screen lock, fingerprint or face is set up.
  notEnrolled,

  /// This phone (or this build) has no way to check.
  unsupported,
}

/// How one check ended. Only [success] unlocks.
enum DeviceAuthResult {
  success,

  /// The person closed the prompt, or the system closed it (a call, the app
  /// went to the background).
  cancelled,

  /// The prompt ended without recognising the person.
  failed,

  /// Too many wrong tries: the system refuses for a short while.
  lockedOut,

  /// Too many wrong tries: biometrics stay off until the person unlocks the
  /// phone with its PIN, pattern or passcode.
  lockedOutUntilDeviceUnlock,

  /// No screen lock, fingerprint or face is set up any more.
  notEnrolled,

  /// The check could not run (no hardware, an error, already running).
  unavailable,
}

abstract interface class DeviceAuthenticator {
  Future<DeviceAuthSupport> support();

  /// Shows the system prompt with [reason]. Biometrics first where set up,
  /// with the screen lock's PIN, pattern or passcode as the fallback. Never
  /// throws: anything unexpected is [DeviceAuthResult.unavailable].
  Future<DeviceAuthResult> authenticate({required String reason});

  /// Closes a prompt that is still open (the app is signing out).
  Future<void> cancel();
}
