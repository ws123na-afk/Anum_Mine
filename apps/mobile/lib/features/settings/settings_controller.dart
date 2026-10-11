import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../admin/admin_repository.dart';
import '../auth/auth_repository.dart';
import '../privacy/privacy_repository.dart';

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
  SettingsController(this.repository, {this.admin, this.privacy});
  final AuthRepository repository;

  /// Members, invitations and model budgets; the API decides who may use them.
  final AdminRepository? admin;

  /// Download my data / Delete my data (docs/privacy.md).
  final PrivacyRepository? privacy;
  SettingsPhase phase = SettingsPhase.initial;
  LocalSession? session;
  ModelConfiguration? model;
  NotificationPreferences notifications = const NotificationPreferences();
  String? message;

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
    notifyListeners();
    try {
      await repository.testModelConnection();
      phase = SettingsPhase.ready;
      message = 'Model provider connection succeeded.';
      notifyListeners();
      return true;
    } on Object catch (error) {
      _fail(error);
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
      await repository.testModelConnection();
      phase = SettingsPhase.ready;
      message = saved.provider == 'anthropic'
          ? 'Saved. ${saved.fastModel} answers chat and voice, '
              '${saved.capableModel} plans tasks.'
          : 'Saved. $model is answering tasks and voice now.';
      notifyListeners();
      return true;
    } on Object catch (error) {
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
    message = text;
    phase = text.contains('(403,')
        ? SettingsPhase.permissionDenied
        : text.toLowerCase().contains('socket') ||
                text.toLowerCase().contains('network')
            ? SettingsPhase.offline
            : SettingsPhase.error;
  }
}

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
