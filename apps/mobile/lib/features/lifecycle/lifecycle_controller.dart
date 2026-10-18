import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'lifecycle_models.dart';
import 'lifecycle_repository.dart';

enum LifecyclePhase { loading, ready, permissionDenied, offline, error }

const lifecyclePermissionMessage =
    'Only active members of this workspace can see its data export and deletion. '
    'Ask a workspace owner to restore your membership, or switch to a workspace you belong to.';

const organisationRightsMessage =
    'Only an organisation administrator who is also an owner of this workspace can export the organisation or schedule its deletion.';

const workspaceRightsMessage =
    'Only an owner of this workspace can schedule or cancel its deletion.';

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
      loadError = describe(error, 'load');
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
      messages[action] = (describe(error, action.name), true);
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
        return 'Export requested. It is prepared in the background; this page updates when it is ready.';
      });

  /// Requests a signed link for a ready export; null when it failed (the
  /// export section shows why).
  Future<LifecycleExportLink?> exportLink(String jobId) async {
    LifecycleExportLink? link;
    await _run(LifecycleAction.link, () async {
      link = await repository.exportLink(jobId);
      return 'Download link ready. It works until ${formatWhen(link!.expiresAt)}.';
    }, jobId: jobId);
    return link;
  }

  Future<bool> scheduleOrganisationDeletion(String confirm) =>
      _run(LifecycleAction.organisationDeletion, () async {
        final job = await repository.scheduleOrganisationDeletion(confirm);
        return 'Organisation deletion scheduled for ${formatWhen(job.purgeAfter)}. You can cancel it until then.';
      });

  Future<bool> cancelOrganisationDeletion() =>
      _run(LifecycleAction.organisationDeletion, () async {
        await repository.cancelOrganisationDeletion();
        return 'Organisation deletion cancelled. Nothing was deleted.';
      });

  Future<bool> scheduleWorkspaceDeletion(String confirmName) =>
      _run(LifecycleAction.workspaceDeletion, () async {
        final job = await repository.scheduleWorkspaceDeletion(confirmName);
        return 'Workspace deletion scheduled for ${formatWhen(job.purgeAfter)}. You can cancel it until then.';
      });

  Future<bool> cancelWorkspaceDeletion() =>
      _run(LifecycleAction.workspaceDeletion, () async {
        await repository.cancelWorkspaceDeletion();
        return 'Workspace deletion cancelled. Nothing was deleted.';
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

  /// What failed and how to fix it.
  static String describe(Object error, String action) {
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'Your sign-in expired. Sign in again, then retry.';
      }
      if (error.isPermissionDenied) {
        if (action == 'load') return lifecyclePermissionMessage;
        // The API names the role needed.
        return error.message.isEmpty || error.message == 'Request failed'
            ? (action == 'workspaceDeletion'
                ? workspaceRightsMessage
                : organisationRightsMessage)
            : error.message;
      }
      if (error.statusCode == 409) {
        return '${error.message} The page was reloaded to show the current state.';
      }
      if (error.statusCode == 410) {
        return 'This export has expired and its archive was deleted. Request a new export.';
      }
      if (error.statusCode == 422) {
        return '${error.readable} Nothing was scheduled.';
      }
    }
    if (_offline(error)) {
      return 'You are offline. Reconnect, then try again.';
    }
    final what = switch (action) {
      'load' => 'Could not load data export and deletion',
      'export' => 'Could not request the export',
      'link' => 'Could not get a download link',
      'organisationDeletion' => 'Could not change the organisation deletion',
      _ => 'Could not change the workspace deletion',
    };
    final reason = error is ApiException ? error.message : error.toString();
    return '$what: $reason. Check your connection and try again.';
  }
}
