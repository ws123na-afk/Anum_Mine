import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../admin/admin_repository.dart';
import '../admin/org_admin_repository.dart';
import '../auth/auth_repository.dart';
import '../autonomy/autonomy_repository.dart';
import '../learning/learning_repository.dart';
import '../lifecycle/lifecycle_repository.dart';
import '../privacy/privacy_repository.dart';
import '../push/push_controller.dart';
import '../team/team_repository.dart';

enum SettingsPhase {
  initial,
  loading,
  ready,
  saving,
  offline,
  permissionDenied,
  error
}

class SettingsController extends ChangeNotifier {
  SettingsController(this.repository,
      {this.admin,
      this.orgAdmin,
      this.privacy,
      this.push,
      this.autonomy,
      this.learning,
      this.team,
      this.lifecycle});
  final AuthRepository repository;

  /// Members, invitations and model budgets; the API decides who may use them.
  final AdminRepository? admin;

  /// Contact opt-outs, this workspace, setup plans and the org chart.
  final OrgAdminRepository? orgAdmin;

  /// Download my data / Delete my data (docs/privacy.md).
  final PrivacyRepository? privacy;

  /// Phone push on this device (docs/notifications.md); null in tests that
  /// do not need it.
  final PushController? push;

  /// Activity and autonomy levels (docs/autonomy.md).
  final AutonomyRepository? autonomy;

  /// What ANUM learned (docs/learning.md).
  final LearningRepository? learning;

  /// Team work, notifications, assistants and organisations (docs/team-work.md).
  final TeamRepository? team;

  /// Organisation export and organisation or workspace deletion
  /// (docs/organisation-lifecycle.md).
  final LifecycleRepository? lifecycle;
  SettingsPhase phase = SettingsPhase.initial;
  LocalSession? session;
  ModelConfiguration? model;
  NotificationPreferences notifications = const NotificationPreferences();
  String? message;

  /// Why the last connection test from the Model connection card failed,
  /// shown in that card; Settings itself stays usable.
  String? modelError;

  /// "Remember things from my chats"; null when it could not be loaded
  /// ([chatMemoryError] says why). Saved on its own, so a failure here never
  /// hides the rest of Settings.
  ChatMemoryPreference? chatMemory;
  String? chatMemoryError;
  String? chatMemoryNotice;
  bool chatMemoryBusy = false;

  Future<void> load() async {
    phase = SettingsPhase.loading;
    message = null;
    notifyListeners();
    try {
      session = await repository.restoreSession();
      if (session == null) {
        throw const _SettingsFailure('Your session has expired.');
      }
      notifications = await repository.notificationPreferences();
      await _loadChatMemory();
      try {
        model = await repository.modelConfiguration();
      } on Object {
        model = null;
      }
      phase = SettingsPhase.ready;
    } on Object catch (error) {
      _fail(error);
    }
    notifyListeners();
  }

  Future<void> saveNotifications(NotificationPreferences value) async {
    phase = SettingsPhase.saving;
    notifyListeners();
    try {
      notifications = await repository.updateNotificationPreferences(value);
      phase = SettingsPhase.ready;
    } on Object catch (error) {
      _fail(error);
    }
    notifyListeners();
  }

  /// What the Email switch can do here; null while unknown (not loaded, or the
  /// server could not say), in which case no hint is shown.
  NotificationChannels? channels;
  EmailTestResult? emailTest;
  String? emailTestError;
  bool emailTestBusy = false;

  Future<void> loadChannels() async {
    try {
      channels = await repository.notificationChannels();
    } on Object {
      channels = null;
    }
    notifyListeners();
  }

  /// Sends the caller a test email; the result (or why it failed) is shown in
  /// the notification card.
  Future<void> sendTestEmail() async {
    emailTestBusy = true;
    emailTest = null;
    emailTestError = null;
    notifyListeners();
    try {
      emailTest = await repository.sendTestEmail();
    } on Object catch (error) {
      emailTestError = 'The test email was not sent. ${_describe(error)}';
    } finally {
      emailTestBusy = false;
      notifyListeners();
    }
  }

  Future<void> _loadChatMemory() async {
    try {
      chatMemory = await repository.chatMemoryPreference();
      chatMemoryError = null;
    } on Object catch (error) {
      chatMemory = null;
      chatMemoryError =
          'Could not load your chat memory setting. ${_describe(error)}';
    }
  }

  Future<void> reloadChatMemory() async {
    chatMemoryBusy = true;
    notifyListeners();
    await _loadChatMemory();
    chatMemoryBusy = false;
    notifyListeners();
  }

  /// Saves the switch at once; on failure the previous choice stays and the
  /// error says what failed.
  Future<void> setRememberConversations(bool remember) async {
    final before = chatMemory;
    if (before == null) return;
    chatMemory = ChatMemoryPreference(
        rememberConversations: remember, state: before.state);
    chatMemoryBusy = true;
    chatMemoryError = null;
    chatMemoryNotice = null;
    notifyListeners();
    try {
      chatMemory = await repository.setRememberConversations(remember);
      chatMemoryNotice = remember
          ? 'Saved. The Assistant will remember things from your chats.'
          : 'Saved. The Assistant will no longer remember or use things from your chats.';
    } on Object catch (error) {
      chatMemory = before;
      chatMemoryError =
          'Your choice was not saved. ${_describe(error)} Try again.';
    }
    chatMemoryBusy = false;
    notifyListeners();
  }

  /// Deletes every memory remembered from this user's chats in the workspace.
  Future<void> deleteChatMemories() async {
    chatMemoryBusy = true;
    chatMemoryError = null;
    chatMemoryNotice = null;
    notifyListeners();
    try {
      final deleted = await repository.deleteMyChatMemories();
      chatMemoryNotice = deleted == 0
          ? 'There were no memories from your chats to delete.'
          : 'Deleted $deleted ${deleted == 1 ? 'memory' : 'memories'} from your chats.';
    } on Object catch (error) {
      chatMemoryError = error is ApiException && error.statusCode == 403
          ? 'Your role cannot delete memories. A workspace owner can delete them or change your role.'
          : 'Nothing was deleted. ${_describe(error)} Try again.';
    }
    chatMemoryBusy = false;
    notifyListeners();
  }

  Future<bool> testModel() async {
    phase = SettingsPhase.saving;
    message = null;
    modelError = null;
    notifyListeners();
    try {
      await repository.testModelConnection();
      phase = SettingsPhase.ready;
      message = 'Model provider connection succeeded.';
      notifyListeners();
      return true;
    } on Object catch (error) {
      // A failed test is about the model, not Settings: say why in the card.
      phase = SettingsPhase.ready;
      modelError = _isForbidden(error)
          ? modelPermissionMessage('test the model connection')
          : 'Connection failed. ${_describe(error)} '
              'Change the model or its key, then test again.';
      notifyListeners();
      return false;
    }
  }

  /// Saves a new model for this workspace and checks it answers. Task runs and
  /// voice answers use it from the next request.
  Future<bool> changeModel({
    required String provider,
    required String model,
    required String baseUrl,
    String? apiKey,
    ModelRouting? routing,
  }) async {
    phase = SettingsPhase.saving;
    message = null;
    notifyListeners();
    try {
      final saved = this.model = await repository.configureModel(
          provider: provider,
          model: model,
          baseUrl: baseUrl,
          apiKey: apiKey,
          routing: routing);
      modelError = null;
      try {
        await repository.testModelConnection();
      } on Object catch (error) {
        if (_isForbidden(error)) {
          throw const _SettingsFailure(
              'The model was saved, but your role cannot test the connection. '
              'An organization owner can test it.');
        }
        rethrow;
      }
      phase = SettingsPhase.ready;
      message = saved.provider == 'anthropic'
          ? 'Saved. ${saved.fastModel} answers chat and voice, '
              '${saved.capableModel} plans tasks.'
          : 'Saved. $model is answering tasks and voice now.';
      notifyListeners();
      return true;
    } on Object catch (error) {
      if (_isForbidden(error)) {
        // Shown in the sheet: who can change the model, not a locked screen.
        phase = SettingsPhase.ready;
        message = modelPermissionMessage('change the model');
        notifyListeners();
        return false;
      }
      _fail(error);
      // A model that does not answer is something to fix in the form, not a
      // reason to hide the rest of Settings.
      if (phase == SettingsPhase.error) phase = SettingsPhase.ready;
      notifyListeners();
      return false;
    }
  }

  Future<void> signOut() => repository.signOut();

  void _fail(Object error) {
    final text = error.toString();
    // The API's own sentence, not "ApiException(404, ...)"; status 0 is the
    // transport's "could not be reached", which is offline.
    message = _describe(error);
    phase = (error is ApiException && error.isPermissionDenied) ||
            text.contains('(403,')
        ? SettingsPhase.permissionDenied
        : (error is ApiException && error.statusCode == 0) ||
                text.toLowerCase().contains('socket') ||
                text.toLowerCase().contains('network')
            ? SettingsPhase.offline
            : SettingsPhase.error;
  }
}

bool _isForbidden(Object error) =>
    error is ApiException && error.isPermissionDenied;

/// What a 403 on the model settings means and who can do it.
String modelPermissionMessage(String action) =>
    'Your role cannot $action. Choosing the model, saving its API key and '
    'testing the connection need the owner role for this organization: ask '
    'an owner to do it, or to change your role.';

String _describe(Object error) {
  final text = error is ApiException ? error.message : error.toString();
  return text.endsWith('.') ? text : '$text.';
}

class _SettingsFailure implements Exception {
  const _SettingsFailure(this.message);
  final String message;
  @override
  String toString() => message;
}
