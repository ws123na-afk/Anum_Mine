import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/readable_error.dart';
import 'contacts_models.dart';
import 'contacts_repository.dart';

enum ContactsPhase { loading, ready, denied, error }

/// The people list: search, 25 at a time with Show more, every state.
class ContactsController extends ChangeNotifier {
  ContactsController(this.repository);
  final ContactsRepository repository;

  ContactsPhase phase = ContactsPhase.loading;
  String? error;
  List<Person> items = const [];
  int total = 0, people = 0, marked = 0, openProposals = 0;
  bool canWrite = false, canManage = false;
  String query = '';
  bool loadingMore = false;
  String? moreError;
  bool _disposed = false;
  int _request = 0;

  bool get hasMore => items.length < total;

  Future<void> search(String query) {
    this.query = query;
    return load();
  }

  Future<void> load() async {
    final id = ++_request;
    phase = ContactsPhase.loading;
    error = moreError = null;
    _notify();
    try {
      final page = await repository.people(query: query);
      if (id != _request) return;
      items = page.items;
      total = page.total;
      people = page.people;
      marked = page.marked;
      openProposals = page.openProposals;
      canWrite = page.canWrite;
      canManage = page.canManage;
      phase = ContactsPhase.ready;
    } on ApiException catch (e) {
      if (id != _request) return;
      phase = e.isPermissionDenied ? ContactsPhase.denied : ContactsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      if (id != _request) return;
      phase = ContactsPhase.error;
      error = readableError(e);
    }
    _notify();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    moreError = null;
    _notify();
    try {
      final page = await repository.people(query: query, offset: items.length);
      items = [...items, ...page.items];
      total = page.total;
    } on Object catch (e) {
      moreError = readableError(e);
    }
    loadingMore = false;
    _notify();
  }

  void reset() {
    items = const [];
    total = 0;
    query = '';
    phase = ContactsPhase.loading;
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// One person's page with the do-not-contact actions.
class PersonController extends ChangeNotifier {
  PersonController(this.repository, this.id);
  final ContactsRepository repository;
  final String id;
  ContactsPhase phase = ContactsPhase.loading;
  String? error;
  PersonDetail? detail;
  bool busy = false;
  String? flash;
  bool _disposed = false;

  Future<void> load() async {
    phase = ContactsPhase.loading;
    error = null;
    _notify();
    try {
      detail = await repository.person(id);
      phase = ContactsPhase.ready;
    } on ApiException catch (e) {
      phase = e.statusCode == 404
          ? ContactsPhase.error
          : e.isPermissionDenied
              ? ContactsPhase.denied
              : ContactsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = ContactsPhase.error;
      error = readableError(e);
    }
    _notify();
  }

  /// Marks or clears a channel; answers the API's sentence on failure, null on success.
  Future<String?> setMark(String channel, String reason,
      {required bool clear}) async {
    busy = true;
    flash = null;
    _notify();
    try {
      if (clear) {
        await repository.clearDoNotContact(id, channel, reason);
      } else {
        await repository.markDoNotContact(id, channel, reason);
      }
      busy = false;
      flash = clear ? 'cleared:$channel' : 'marked:$channel';
      await load();
      return null;
    } on Object catch (e) {
      busy = false;
      _notify();
      return readableError(e);
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
