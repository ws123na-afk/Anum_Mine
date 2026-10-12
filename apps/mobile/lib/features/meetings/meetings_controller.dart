import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../workspace/files_repository.dart'
    show UploadCancellation, UploadCancelledException;
import 'meetings_models.dart';
import 'meetings_repository.dart';

enum MeetingsPhase { loading, ready, denied, error }

/// The Calls & Meetings list: search, status filter, paging, recent
/// activity and file imports (docs/meetings.md#mobile).
class MeetingsController extends ChangeNotifier {
  MeetingsController(
    this.repository, {
    this.pollEvery = const Duration(seconds: 5),
  });
  final MeetingsRepository repository;

  /// How often the list refreshes while a meeting is being processed; null
  /// turns polling off (tests).
  final Duration? pollEvery;

  MeetingsPhase phase = MeetingsPhase.loading;
  MeetingSetup? setup;
  String? error, notice;
  List<Meeting> items = const [];
  int total = 0;
  String query = '', status = '';
  bool loadingMore = false;
  (int, int)? uploadProgress;
  UploadCancellation? _upload;
  Timer? _poll;
  bool _disposed = false;

  bool get canCreate => setup?.canCreate ?? false;
  bool get hasMore => items.length < total;

  /// "Call summary" entries: the newest meetings with notes.
  List<Meeting> get recentActivity {
    final ready = items
        .where((m) => m.status == 'ready' && m.summary != null)
        .toList()
      ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return ready.take(5).toList(growable: false);
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    if (items.isEmpty) phase = MeetingsPhase.loading;
    _notify();
    try {
      setup ??= await repository.setup();
    } on Object {
      // The list still loads; import options stay hidden until setup reads.
    }
    try {
      final page = await repository.list(query: query, status: status);
      items = page.items;
      total = page.total;
      phase = MeetingsPhase.ready;
      error = null;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? MeetingsPhase.denied : MeetingsPhase.error;
      error = e.message;
    } on Object catch (e) {
      phase = MeetingsPhase.error;
      error = '$e';
    }
    _schedulePoll();
    _notify();
  }

  Future<void> refreshSetup() async {
    try {
      setup = await repository.setup();
    } on Object {
      return;
    }
    _notify();
  }

  void _schedulePoll() {
    _poll?.cancel();
    final every = pollEvery;
    if (every == null || !items.any((m) => m.isWorking)) return;
    _poll = Timer(every, load);
  }

  Future<void> search(String value) {
    query = value.trim();
    return load();
  }

  Future<void> filter(String value) {
    status = value;
    return load();
  }

  Future<void> loadMore() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    _notify();
    try {
      final page = await repository.list(
        query: query,
        status: status,
        offset: items.length,
      );
      final seen = items.map((m) => m.id).toSet();
      items = [...items, ...page.items.where((m) => !seen.contains(m.id))];
      total = page.total;
    } on Object catch (e) {
      notice = e is ApiException ? e.message : '$e';
    } finally {
      loadingMore = false;
      _notify();
    }
  }

  /// Uploads a transcript or recording; returns the new meeting or null when
  /// it failed (see [notice]) or was cancelled.
  Future<Meeting?> importFile({
    required bool recording,
    required String path,
    required String name,
    String title = '',
    String language = 'auto',
  }) async {
    final cancellation = UploadCancellation();
    _upload = cancellation;
    uploadProgress = (0, 0);
    notice = null;
    _notify();
    try {
      final meeting = await repository.importFile(
        recording: recording,
        path: path,
        name: name,
        title: title,
        language: language,
        onProgress: (sent, total) {
          uploadProgress = (sent, total);
          _notify();
        },
        cancellation: cancellation,
      );
      await load();
      return meeting;
    } on UploadCancelledException {
      notice = 'cancelled';
      return null;
    } on ApiException catch (e) {
      notice = e.message;
      return null;
    } finally {
      uploadProgress = null;
      _upload = null;
      _notify();
    }
  }

  void cancelUpload() => _upload?.cancel();

  void replace(Meeting meeting) {
    items = [for (final m in items) m.id == meeting.id ? meeting : m];
    _notify();
  }

  void removed(String id) {
    items = items.where((m) => m.id != id).toList(growable: false);
    total = total > 0 ? total - 1 : 0;
    _notify();
  }

  @override
  void dispose() {
    _disposed = true;
    _poll?.cancel();
    super.dispose();
  }
}
