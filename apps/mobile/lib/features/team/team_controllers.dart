import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'team_models.dart';
import 'team_repository.dart';

enum TeamPhase { loading, ready, error, denied }

/// Loads one value and remembers how it went (every screen's loading, error
/// and permission-denied states come from here).
class TeamLoader<T> extends ChangeNotifier {
  TeamLoader(this._fetch);
  final Future<T> Function() _fetch;
  TeamPhase phase = TeamPhase.loading;
  T? value;
  String? error;
  bool _disposed = false;

  Future<void> load() async {
    phase = TeamPhase.loading;
    _notify();
    try {
      value = await _fetch();
      phase = TeamPhase.ready;
      error = null;
    } catch (e) {
      phase = e is ApiException && e.isPermissionDenied
          ? TeamPhase.denied
          : TeamPhase.error;
      error = describeTeamError(e);
    }
    _notify();
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

/// A paged list of work items: My tasks (`scope=mine`) or the team's open work.
class WorkListController extends ChangeNotifier {
  WorkListController(this.repository, {this.scope = 'mine'});
  final TeamRepository repository;
  final String scope;
  TeamPhase phase = TeamPhase.loading;
  List<WorkItem> items = [];
  int total = 0;
  int? nextOffset;
  String query = '';
  String? error;
  bool loadingMore = false;
  bool _disposed = false;

  Future<void> load() async {
    phase = TeamPhase.loading;
    _notify();
    try {
      final page = await repository.work(scope: scope, query: query);
      items = page.items;
      total = page.total;
      nextOffset = page.nextOffset;
      phase = TeamPhase.ready;
      error = null;
    } catch (e) {
      phase = e is ApiException && e.isPermissionDenied
          ? TeamPhase.denied
          : TeamPhase.error;
      error = describeTeamError(e);
    }
    _notify();
  }

  Future<void> search(String value) {
    query = value;
    return load();
  }

  Future<void> loadMore() async {
    final offset = nextOffset;
    if (offset == null || loadingMore) return;
    loadingMore = true;
    _notify();
    try {
      final page = await repository.work(
        scope: scope,
        query: query,
        offset: offset,
      );
      items = [...items, ...page.items];
      nextOffset = page.nextOffset;
    } catch (e) {
      error = describeTeamError(e);
    }
    loadingMore = false;
    _notify();
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

/// One work item: status changes, checklist ticks and comments.
class WorkDetailController extends ChangeNotifier {
  WorkDetailController(this.repository, this.itemId);
  final TeamRepository repository;
  final String itemId;
  WorkItem? item;
  TeamPhase phase = TeamPhase.loading;
  String? error;
  String? flash;
  bool busy = false;
  bool _disposed = false;

  Future<void> load() async {
    try {
      item = await repository.item(itemId);
      phase = TeamPhase.ready;
      error = null;
    } catch (e) {
      phase = TeamPhase.error;
      error = describeTeamError(e);
    }
    _notify();
  }

  Future<void> _act(Future<void> Function() action) async {
    busy = true;
    flash = null;
    _notify();
    try {
      await action();
      await load();
    } on ApiException catch (e) {
      flash = e.statusCode == 409
          ? 'conflict:${e.message}'
          : e.isPermissionDenied
              ? 'denied:${e.message}'
              : 'error:${e.message}';
      if (e.statusCode == 409) await load();
    } catch (e) {
      flash = 'error:${describeTeamError(e)}';
    }
    busy = false;
    _notify();
  }

  Future<void> setStatus(String status) {
    final current = item;
    if (current == null) return Future.value();
    return _act(() => repository.setStatus(current, status));
  }

  Future<void> tick(ChecklistEntry entry, bool done) =>
      _act(() => repository.tick(itemId, entry.id, done));

  Future<void> comment(String body) =>
      _act(() => repository.comment(itemId, body));

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
