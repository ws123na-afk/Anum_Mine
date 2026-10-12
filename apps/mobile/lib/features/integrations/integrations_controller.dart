import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'integrations_models.dart';
import 'integrations_repository.dart';

enum IntegrationsPhase { loading, ready, error, denied }

/// The Integrations list (mockup 12): every integration the API reports,
/// the All / Connected / Available tabs, search, and the owner's actions.
class IntegrationsController extends ChangeNotifier {
  IntegrationsController(this.repository);
  final IntegrationsRepository repository;

  IntegrationsPhase phase = IntegrationsPhase.loading;

  /// Why the list could not load (shown through `describeIntegrationError`).
  Object? error;
  List<Integration> items = const [];
  IntegrationTab tab = IntegrationTab.all;
  String query = '';
  String? role;
  bool started = false;

  /// Integrations with a change in flight (switch, test, credential).
  final Set<String> busy = {};
  int _generation = 0;

  bool get isOwner => role == 'owner';

  List<Integration> get visible {
    final q = query.trim().toLowerCase();
    return items
        .where((i) =>
            integrationInTab(i, tab) &&
            (q.isEmpty ||
                i.name.toLowerCase().contains(q) ||
                i.kind.toLowerCase().contains(q) ||
                i.id.toLowerCase().contains(q)))
        .toList();
  }

  int count(IntegrationTab value) =>
      items.where((i) => integrationInTab(i, value)).length;

  Future<void> start() async {
    if (started) return;
    started = true;
    unawaited(repository.viewer().then((v) {
      role = v.role;
      notifyListeners();
    }, onError: (_) {}));
    await refresh();
  }

  Future<void> refresh() async {
    final generation = ++_generation;
    if (items.isEmpty) phase = IntegrationsPhase.loading;
    notifyListeners();
    try {
      final loaded = await repository.list();
      if (generation != _generation) return;
      items = [...loaded]..sort((a, b) {
          // Connected first, then by name, so what works is on top.
          final c = (b.connected ? 1 : 0) - (a.connected ? 1 : 0);
          return c != 0
              ? c
              : a.name.toLowerCase().compareTo(b.name.toLowerCase());
        });
      phase = IntegrationsPhase.ready;
      error = null;
    } catch (e) {
      if (generation != _generation) return;
      phase = e is ApiException && e.isPermissionDenied
          ? IntegrationsPhase.denied
          : IntegrationsPhase.error;
      error = e;
    }
    notifyListeners();
  }

  void select(IntegrationTab value) {
    tab = value;
    notifyListeners();
  }

  void search(String value) {
    query = value;
    notifyListeners();
  }

  void clearFilters() {
    query = '';
    tab = IntegrationTab.all;
    notifyListeners();
  }

  Integration? byId(String id) {
    for (final item in items) {
      if (item.id == id) return item;
    }
    return null;
  }

  /// Runs an owner action on [id] and reloads; null on success, else the
  /// error to describe.
  Future<Object?> act(String id, Future<void> Function() action) async {
    if (busy.contains(id)) return null;
    busy.add(id);
    notifyListeners();
    try {
      await action();
      await refresh();
      return null;
    } catch (e) {
      return e;
    } finally {
      busy.remove(id);
      notifyListeners();
    }
  }

  Future<Object?> setEnabled(String id, bool enabled) =>
      act(id, () => repository.setEnabled(id, enabled));

  Future<Object?> test(String id) => act(id, () => repository.test(id));

  Future<Object?> storeCredential(String id, String secret) =>
      act(id, () => repository.storeCredential(id, secret));

  Future<Object?> removeCredential(String id) =>
      act(id, () => repository.removeCredential(id));

  /// Forget everything (sign-out or workspace switch).
  void reset() {
    _generation++;
    started = false;
    phase = IntegrationsPhase.loading;
    items = const [];
    role = null;
    error = null;
    query = '';
    tab = IntegrationTab.all;
    notifyListeners();
  }
}
