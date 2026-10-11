import 'dart:async';

import 'package:flutter/foundation.dart' hide shortHash;

import '../../data/api_client.dart';
import '../workspace/approvals_screen.dart' show shortHash;
import 'developer_models.dart';
import 'developer_repository.dart';

/// Developer mode from the Assistant chat and the voice view
/// (docs/developer-mode.md). Mirrors the web `useDeveloperAssist`.
///
/// "developer, ...", "build ...", "Anum build ..." or "ANUM, in PROJECT
/// ..." first resolves the project on the server (by name, by description,
/// or the only ready one). When it is clear it always confirms ("I'll ask
/// the developer to: ... in PROJECT. Start?"); when it is not, it asks
/// which project (buttons, or a spoken name) and then confirms. The request
/// is created only after Start or a spoken "yes". When a pull request
/// becomes ready it is announced; "approve" opens the merge approval with
/// its payload hash, which still needs a tap on Confirm merge: nothing is
/// ever approved by voice alone.
class DeveloperAssist extends ChangeNotifier {
  DeveloperAssist({
    required this.repository,
    required this.source,
    required this.isOwner,
  });

  final DeveloperRepository repository;

  /// `text` for the chat, `voice` for the voice view; stored on the request.
  final String source;

  /// Resolves whether the caller is an active owner (shared with the
  /// Developer screen, so the role is fetched once).
  final Future<bool> Function() isOwner;

  /// Said (voice) or shown (chat) when a pull request becomes ready.
  void Function(String text)? announce;

  /// The request waiting for an answer (which project, or Start), or null.
  String? pending;

  /// The project [pending] goes to once started; null while [choices] asks.
  ProjectRef? pendingProject;

  /// The projects to choose from while the request names none clearly.
  List<ProjectRef> choices = const [];

  /// Asking "Which project?" rather than "Start?".
  bool get choosing => pending != null && pendingProject == null;

  /// The latest sentence to show.
  String? message;

  /// The request whose pull request just became ready for review.
  DevRequest? ready;

  /// The merge approval opened for review; merging needs [confirmMerge].
  MergeReview? review;
  bool busy = false;

  /// The latest requests, or null before the first poll.
  List<DevRequest>? requests;

  List<DevRequest>? _previous;
  bool _active = false;
  bool _disposed = false;
  Timer? _poll;

  bool get awaitingAnswer => pending != null;

  /// Anything to render near the composer.
  bool get hasPanel => pending != null || message != null || review != null;

  /// The screen is on screen: poll for ready pull requests (owners only).
  void setActive(bool value) {
    if (_active == value) return;
    _active = value;
    if (!value) {
      _poll?.cancel();
      return;
    }
    unawaited(() async {
      if (await isOwner() && _active && !_disposed) await poll();
    }());
  }

  /// Reads the latest requests and announces the ones that just became ready.
  Future<void> poll() async {
    _poll?.cancel();
    try {
      final current = await repository.list(limit: 10);
      if (_disposed) return;
      for (final item in newlyReady(_previous, current)) {
        final text = readyAnnouncement(item);
        ready = item;
        message = text;
        announce?.call(text);
      }
      _previous = current;
      requests = current;
    } on Object {
      // Developer mode may be unconfigured or the API unreachable; the chat
      // and voice keep working.
      requests ??= const [];
    }
    _notify();
    _schedule();
  }

  void _schedule() {
    _poll?.cancel();
    final current = requests;
    if (!_active || _disposed || current == null) return;
    final delay = pollDelay(current);
    if (delay == null) return;
    _poll = Timer(delay, () {
      if (_active && !_disposed) unawaited(poll());
    });
  }

  /// Handles a developer command or an answer to a pending question.
  /// Resolves with the reply, or null when [text] is not for the developer.
  Future<String?> intercept(String text) async {
    final waitingFor = pending;
    if (waitingFor != null && pendingProject != null) {
      switch (parseConfirmation(text)) {
        case Confirmation.yes:
          return start(waitingFor);
        case Confirmation.no:
          return _say(declined());
        case null:
          _clearPending();
          _notify();
      }
    } else if (waitingFor != null) {
      if (parseConfirmation(text) == Confirmation.no) return _say(declined());
      final chosen = matchProjectAnswer(text, choices);
      if (chosen != null) return choose(chosen);
      if (parseDeveloperCommand(text) == null) {
        return _say(whichProjectAgain(choices));
      }
      _clearPending();
      _notify();
    }
    if (isApproveIntent(text)) {
      final waiting = ready ??
          requests
              ?.where((item) => item.status == DevStatus.waitingApproval)
              .firstOrNull;
      if (waiting != null) {
        if (!await isOwner()) return _say(ownerOnlyReply);
        return openReview(waiting);
      }
    }
    final command = parseDeveloperCommand(text);
    if (command == null) return null;
    if (!await isOwner()) return _say(ownerOnlyReply);
    busy = true;
    _notify();
    try {
      final resolution = await repository.resolve(command);
      switch (resolution.status) {
        case ResolutionStatus.none:
          return _say(noProjectsReply);
        case ResolutionStatus.resolved when resolution.project != null:
          return _confirm(resolution.text, resolution.project!);
        case _:
          pending = resolution.text;
          pendingProject = null;
          choices = resolution.candidates;
          message = null;
          return whichProjectPrompt(choices);
      }
    } on Object catch (error) {
      return _say(
          'I could not check your projects: ${explainDeveloperError(error)}');
    } finally {
      busy = false;
      _notify();
    }
  }

  String _confirm(String text, ProjectRef project) {
    pending = text;
    pendingProject = project;
    choices = const [];
    message = null;
    _notify();
    return confirmPrompt(text, project.name);
  }

  /// A project picked from [choices] (a tap or a spoken name): confirm next.
  String choose(ProjectRef project) => _confirm(pending ?? '', project);

  void _clearPending() {
    pending = null;
    pendingProject = null;
    choices = const [];
  }

  /// "Okay, I won't start it."
  String declined() {
    _clearPending();
    return "Okay, I won't start it.";
  }

  /// The Cancel button on the confirmation.
  void decline() => _say(declined());

  /// Start (or a spoken "yes"): creates the request in [pendingProject].
  Future<String> start(String text) async {
    final project = pendingProject;
    _clearPending();
    if (project == null) return _say(noProjectsReply);
    if (mentionsProtected(text)) {
      return _say('That touches CI workflows or secret handling. Open the '
          'Developer screen to send it with the explicit allow.');
    }
    busy = true;
    _notify();
    try {
      final created =
          await repository.create(text, source: source, projectId: project.id);
      requests = [
        created,
        for (final item in requests ?? const <DevRequest>[])
          if (item.id != created.id) item
      ];
      _previous = [created, ...?_previous];
      return _say('Started in ${project.name}. The developer is on it; '
          "I'll tell you when the pull request is ready for review.");
    } on Object catch (error) {
      return _say(
          'The developer could not start: ${explainDeveloperError(error)}');
    } finally {
      busy = false;
      _notify();
      _schedule();
    }
  }

  /// Opens the merge approval with its payload hash. Never merges.
  Future<String> openReview(DevRequest request) async {
    busy = true;
    _notify();
    try {
      final result = await repository.requestMerge(request.id);
      review = result;
      final where = result.request.projectName ?? result.request.repository;
      return _say('Here is the merge to approve: pull request '
          '#${result.request.prNumber}${where == null ? '' : ' in $where'}, '
          'payload hash '
          '${shortHash(result.approval.payloadHash)}. Tap Confirm merge on '
          'screen; I never approve by voice.');
    } on Object catch (error) {
      return _say('The merge is not ready: ${explainDeveloperError(error)}');
    } finally {
      busy = false;
      _notify();
    }
  }

  /// The tap on Confirm merge: approves with the displayed payload hash.
  Future<void> confirmMerge() async {
    final current = review;
    if (current == null || busy || current.approval.payloadHash == null) {
      return;
    }
    busy = true;
    _notify();
    try {
      final result = await repository.approve(current.approval);
      final updated = await repository.get(current.request.id, refresh: true);
      review = null;
      ready = null;
      message = updated.status == DevStatus.merged
          ? 'Merged pull request #${updated.prNumber}.'
          : updated.mergeError ??
              result ??
              'The merge did not happen. Open the Developer screen for '
                  'details.';
    } on Object catch (error) {
      message = error is ApiException && error.isPermissionDenied
          ? 'Merge refused: ${error.message}'
          : 'Not merged: ${explainDeveloperError(error)}';
    } finally {
      busy = false;
      _notify();
    }
  }

  /// "Not now" on the merge approval; it stays in Approvals until it lapses.
  void dismissReview() {
    review = null;
    _notify();
  }

  void dismiss() {
    message = null;
    ready = null;
    _notify();
  }

  /// Forgets everything (sign-out or a workspace switch).
  void reset() {
    _poll?.cancel();
    _clearPending();
    message = null;
    ready = null;
    review = null;
    busy = false;
    requests = null;
    _previous = null;
    _notify();
  }

  String _say(String text) {
    message = text;
    _notify();
    return text;
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _poll?.cancel();
    super.dispose();
  }
}
