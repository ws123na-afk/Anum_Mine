import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'calls_models.dart';
import 'calls_repository.dart';

enum CallsPhase { loading, ready, denied, error }

/// One paged, searchable list (contacts or the call log).
class CallsList<T> extends ChangeNotifier {
  CallsList(this._fetch);
  final Future<CallsPage<T>> Function(String query, int offset) _fetch;

  CallsPhase phase = CallsPhase.loading;
  List<T> items = const [];
  int total = 0;
  String query = '';
  String? error;
  bool loadingMore = false;
  bool _disposed = false;

  bool get hasMore => items.length < total;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load({String? query}) async {
    if (query != null) this.query = query;
    phase = CallsPhase.loading;
    _notify();
    try {
      final page = await _fetch(this.query, 0);
      items = page.items;
      total = page.total;
      phase = CallsPhase.ready;
      error = null;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? CallsPhase.denied : CallsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = CallsPhase.error;
      error = '$e';
    }
    _notify();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    _notify();
    try {
      final page = await _fetch(query, items.length);
      items = [...items, ...page.items];
      total = page.total;
    } on Object catch (e) {
      error = e is ApiException ? e.readable : '$e';
    } finally {
      loadingMore = false;
      _notify();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// The Calls screen's state: setup, contacts, the call log, and the call the
/// phone's dialer was opened for (logged when the person comes back).
class CallsController extends ChangeNotifier {
  CallsController(this.repository) {
    contacts = CallsList<CallContact>((query, offset) =>
        repository.contacts(query: query, offset: offset));
    recents = CallsList<CallEntry>(
        (query, offset) => repository.calls(query: query, offset: offset));
  }
  final CallsRepository repository;
  late final CallsList<CallContact> contacts;
  late final CallsList<CallEntry> recents;
  CallsSetup? setup;
  String? setupError;
  bool setupDenied = false;

  /// The dialer was opened at this time for this number: ask to log it.
  PendingDial? pending;

  bool _disposed = false;
  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    try {
      setup = await repository.setup();
      setupError = null;
      setupDenied = false;
    } on ApiException catch (e) {
      setupDenied = e.isPermissionDenied;
      setupError = e.readable;
    } on Object catch (e) {
      setupError = '$e';
    }
    _notify();
    if (!setupDenied) {
      await Future.wait([contacts.load(), recents.load()]);
    }
  }

  void dialed(String phone, CallContact? contact, DateTime at) {
    pending = PendingDial(phone: phone, contact: contact, startedAt: at);
    _notify();
  }

  void clearPending() {
    pending = null;
    _notify();
  }

  void reset() {
    setup = null;
    pending = null;
  }

  @override
  void dispose() {
    _disposed = true;
    contacts.dispose();
    recents.dispose();
    super.dispose();
  }
}

class PendingDial {
  const PendingDial(
      {required this.phone, required this.contact, required this.startedAt});
  final String phone;
  final CallContact? contact;
  final DateTime startedAt;
}
