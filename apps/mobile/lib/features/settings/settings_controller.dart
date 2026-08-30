import 'package:flutter/foundation.dart';

import '../../data/api_models.dart';
import '../auth/auth_repository.dart';

enum SettingsPhase { initial, loading, ready, saving, offline, permissionDenied, error }

class SettingsController extends ChangeNotifier {
  SettingsController(this.repository);
  final AuthRepository repository;
  SettingsPhase phase = SettingsPhase.initial;
  LocalSession? session;
  ModelConfiguration? model;
  NotificationPreferences notifications = const NotificationPreferences();
  String? message;

  Future<void> load() async {
    phase = SettingsPhase.loading; message = null; notifyListeners();
    try {
      session = await repository.restoreSession();
      if (session == null) throw const _SettingsFailure('Your session has expired.');
      notifications = await repository.notificationPreferences();
      try { model = await repository.modelConfiguration(); } on Object { model = null; }
      phase = SettingsPhase.ready;
    } on Object catch (error) { _fail(error); }
    notifyListeners();
  }

  Future<void> saveNotifications(NotificationPreferences value) async {
    phase = SettingsPhase.saving; notifyListeners();
    try { notifications = await repository.updateNotificationPreferences(value); phase = SettingsPhase.ready; }
    on Object catch (error) { _fail(error); }
    notifyListeners();
  }

  Future<bool> testModel() async {
    phase = SettingsPhase.saving; message = null; notifyListeners();
    try { await repository.testModelConnection(); phase = SettingsPhase.ready; message = 'Model provider connection succeeded.'; notifyListeners(); return true; }
    on Object catch (error) { _fail(error); notifyListeners(); return false; }
  }

  Future<void> signOut() => repository.signOut();

  void _fail(Object error) {
    final text = error.toString(); message = text;
    phase = text.contains('(403,') ? SettingsPhase.permissionDenied : text.toLowerCase().contains('socket') || text.toLowerCase().contains('network') ? SettingsPhase.offline : SettingsPhase.error;
  }
}

class _SettingsFailure implements Exception { const _SettingsFailure(this.message); final String message; @override String toString()=>message; }
