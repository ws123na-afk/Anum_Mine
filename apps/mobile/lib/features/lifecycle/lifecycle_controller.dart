import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'lifecycle_models.dart';
import 'lifecycle_strings.dart';
import 'lifecycle_repository.dart';

enum LifecyclePhase { loading, ready, permissionDenied, offline, error }

final lifecyclePermissionMessage = LifecycleStrings.english.t('permission');
final organisationRightsMessage = LifecycleStrings.english.t('orgRights');
final workspaceRightsMessage = LifecycleStrings.english.t('wsRights');

/// Which action a message belongs to, so each section shows its own result.
enum LifecycleAction {
  export,
  link,
  organisationDeletion,
  workspaceDeletion,
}

/// Settings -> Data export and deletion (docs/organisation-lifecycle.md).
/// Polls every [pollInterval] while an export is queued or running.
class LifecycleController extends ChangeNotifier {
  LifecycleController(this.repository,
      {this.pollInterval = const Duration(seconds: 5)});
  final LifecycleRepository repository;
  final Duration pollInterval;

  /// The language of the sentences this controller writes; the screen sets
  /// it from the app's locale.
  LifecycleStrings strings = LifecycleStrings.english;

  LifecyclePhase phase = LifecyclePhase.loading;
  LifecycleSummary? summary;
  String? loadError;

  /// The action running now (one at a time), and the export whose link is
  /// being requested.
  LifecycleAction? busy;
  String? linkJobId;

  /// The last result per action: the sentence and whether it failed.
  final Map<LifecycleAction, (String, bool)> messages = {};

  Timer? _poll;
  bool _disposed = false;

  Future<void> load() async {
    phase = LifecyclePhase.loading;
    _notify();
    try {
      summary = await repository.summary();
      phase = LifecyclePhase.ready;
    } on Object catch (error) {
      loadError = describe(error, 'load', strings);
      phase = error is ApiException && error.isPermissionDenied
          ? LifecyclePhase.permissionDenied
          : _offline(error)
              ? LifecyclePhase.offline
              : LifecyclePhase.error;
    }
    _schedulePoll();
    _notify();
  }

  /// Reloads the summary without leaving the page; a failure keeps what is
  /// shown and only stops the poll.
  Future<void> refresh() async {
    try {
      summary = await repository.summary();
      phase = LifecyclePhase.ready;
    } on Object {
      // The page keeps the last summary; Retry is a pull to refresh away.
    }
    _schedulePoll();
    _notify();
  }

  void _schedulePoll() {
    _poll?.cancel();
    _poll = null;
    if (_disposed || !(summary?.exportInProgress ?? false)) return;
    _poll = Timer(pollInterval, refresh);
  }

  /// True while the page polls for an export to finish.
  bool get polling => _poll?.isActive ?? false;

  Future<bool> _run(LifecycleAction action, Future<String> Function() work,
      {String? jobId}) async {
    busy = action;
    linkJobId = jobId;
    messages.remove(action);
    _notify();
    var ok = false, stale = false;
    try {
      messages[action] = (await work(), false);
      ok = true;
    } on Object catch (error) {
      messages[action] = (describe(error, action.name, strings), true);
      // A 409 or 410 means the page is out of date: reload to show what is
      // true now.
      stale = error is ApiException &&
          (error.statusCode == 409 || error.statusCode == 410);
    }
    busy = null;
    linkJobId = null;
    if ((ok && action != LifecycleAction.link) || stale) await refresh();
    _notify();
    return ok;
  }

  Future<bool> requestExport() => _run(LifecycleAction.export, () async {
        await repository.requestExport();
        return strings.t('done.export');
      });

  /// Requests a signed link for a ready export; null when it failed (the
  /// export section shows why).
  Future<LifecycleExportLink?> exportLink(String jobId) async {
    LifecycleExportLink? link;
    await _run(LifecycleAction.link, () async {
      link = await repository.exportLink(jobId);
      return strings
          .t('done.link', {'when': formatWhen(link!.expiresAt, strings)});
    }, jobId: jobId);
    return link;
  }

  Future<bool> scheduleOrganisationDeletion(String confirm) =>
      _run(LifecycleAction.organisationDeletion, () async {
        final job = await repository.scheduleOrganisationDeletion(confirm);
        return strings.t(
            'done.orgScheduled', {'when': formatWhen(job.purgeAfter, strings)});
      });

  Future<bool> cancelOrganisationDeletion() =>
      _run(LifecycleAction.organisationDeletion, () async {
        await repository.cancelOrganisationDeletion();
        return strings.t('done.orgCancelled');
      });

  Future<bool> scheduleWorkspaceDeletion(String confirmName) =>
      _run(LifecycleAction.workspaceDeletion, () async {
        final job = await repository.scheduleWorkspaceDeletion(confirmName);
        return strings.t(
            'done.wsScheduled', {'when': formatWhen(job.purgeAfter, strings)});
      });

  Future<bool> cancelWorkspaceDeletion() =>
      _run(LifecycleAction.workspaceDeletion, () async {
        await repository.cancelWorkspaceDeletion();
        return strings.t('done.wsCancelled');
      });

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _poll?.cancel();
    _poll = null;
    super.dispose();
  }

  static bool _offline(Object error) {
    final text = error.toString().toLowerCase();
    return text.contains('socket') || text.contains('network');
  }

  /// What failed and how to fix it, in [s]'s language.
  static String describe(Object error, String action,
      [LifecycleStrings s = LifecycleStrings.english]) {
    if (error is ApiException) {
      if (error.statusCode == 401) return s.t('error.signIn');
      if (error.isPermissionDenied) {
        if (action == 'load') return s.t('permission');
        // The API names the role needed.
        return error.message.isEmpty || error.message == 'Request failed'
            ? s.t(action == 'workspaceDeletion' ? 'wsRights' : 'orgRights')
            : error.message;
      }
      if (error.statusCode == 409) {
        return s.t('error.reloaded', {'message': error.message});
      }
      if (error.statusCode == 410) return s.t('error.expired');
      if (error.statusCode == 422) {
        return s.t('error.nothingScheduled', {'message': error.readable});
      }
    }
    if (_offline(error)) return s.t('error.offline');
    final what = s.t(switch (action) {
      'load' => 'error.load',
      'export' => 'error.export',
      'link' => 'error.link',
      'organisationDeletion' => 'error.organisationDeletion',
      _ => 'error.workspaceDeletion',
    });
    final reason = error is ApiException ? error.message : error.toString();
    return s.t('error.generic', {'what': what, 'reason': reason});
  }
}
