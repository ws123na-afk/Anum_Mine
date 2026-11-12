// The phone's own call screen for incoming team calls (docs/in-app-calls.md#system-call-screens):
// CallKit on iOS and a self-managed ConnectionService on Android. This is the
// boundary the app's call layer talks to. The build ships [NoSystemCallScreen]:
// no native call-screen plugin is linked yet (docs/pending-work.md, "Team
// calls"), so an incoming call rings in the app while it is open and as a
// normal push otherwise, exactly as before.
//
// A plugin implementation (flutter_callkit_incoming was checked: it applies the
// Kotlin Gradle plugin itself, so it builds with android.builtInKotlin=false)
// implements this interface; the controller already shows, dismisses and
// reports calls through it and answers or declines on its actions.
import 'dart:async';

/// What the person did on the phone's own call screen.
sealed class SystemCallAction {
  const SystemCallAction(this.callId);
  final String callId;
}

class SystemCallAccepted extends SystemCallAction {
  const SystemCallAccepted(super.callId);
}

class SystemCallDeclined extends SystemCallAction {
  const SystemCallDeclined(super.callId);
}

class SystemCallEnded extends SystemCallAction {
  const SystemCallEnded(super.callId);
}

abstract interface class SystemCallScreen {
  /// Whether the phone's own call screen is used at all.
  bool get available;

  /// Rings on the phone's own screen (lock screen included) while the app is
  /// in the background. [callerName] is shown; nothing else about the call.
  Future<void> showIncoming(
      {required String callId,
      required String callerName,
      required bool video,
      required Duration ringFor});

  /// The ring stopped: answered elsewhere, cancelled, missed or declined here.
  Future<void> dismiss(String callId);

  /// The call connected (the system shows it as ongoing) or ended.
  Future<void> reportConnected(String callId);
  Future<void> reportEnded(String callId);

  Stream<SystemCallAction> get actions;
}

/// The default: no system call screen; the app's own ring is used.
class NoSystemCallScreen implements SystemCallScreen {
  const NoSystemCallScreen();

  @override
  bool get available => false;

  @override
  Future<void> showIncoming(
      {required String callId,
      required String callerName,
      required bool video,
      required Duration ringFor}) async {}

  @override
  Future<void> dismiss(String callId) async {}

  @override
  Future<void> reportConnected(String callId) async {}

  @override
  Future<void> reportEnded(String callId) async {}

  @override
  Stream<SystemCallAction> get actions => const Stream.empty();
}
