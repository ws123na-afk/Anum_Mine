import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../workspace/workspace_models.dart';
import 'developer_models.dart';
import 'developer_repository.dart';

/// Whether the caller may use developer mode: unknown until the membership
/// loads, then owner or not. The API checks again on every call.
enum OwnerState { unknown, loading, owner, notOwner, error }

enum DevPhase { initial, loading, ready, error, forbidden }

/// The Developer screen's state (docs/developer-mode.md): the caller's role,
/// the setup, the request list with search and paging, and the actions.
/// Mirrors the web `DeveloperView`.
class DeveloperController extends ChangeNotifier {
  DeveloperController(this.repository);

  /// The language of the notices this controller writes; the screens set it.
  AnumLocalizations l = AnumLocalizations.english;

  final DeveloperRepository repository;

  static const pageSize = 20;

  OwnerState ownerState = OwnerState.unknown;

  /// The role the membership reported, for the owner-only explanation.
  String? role;
  String? roleError;

  DevPhase phase = DevPhase.initial;

  /// A load is in flight (first load or a refresh).
  bool refreshing = false;
  String? loadError;
  DevSetup? setup;
  List<DevRequest> items = const [];
  bool hasMore = false;

  /// The workspace's projects (GitHub repositories), by name.
  List<DevProject> projects = const [];

  /// The project the request form sends to; null until one is chosen.
  String? projectId;

  /// The project used for the last request, preferred next time.
  String? _lastProject;

  /// The search the list shows; empty for all requests.
  String query = '';

  /// Which action runs: `create`, `more`, `refresh:<id>`, `cancel:<id>`,
  /// `review:<id>` or `merge:<id>`.
  String? busy;
  String? notice;
  bool noticeFailed = false;

  /// Set when a request is refused because the monthly model budget is used up.
  String? budget;

  /// Merge approvals opened here, by request id.
  final Map<String, WorkspaceApproval> _reviews = {};

  /// "Not now" hides a pending merge approval here; it stays in Approvals
  /// until it lapses.
  final Set<String> _dismissed = {};

  bool _active = false;
  bool _disposed = false;
  Timer? _poll;
  Future<bool>? _roleLoad;
  String? _workspaceId;
  int _loadToken = 0;

  bool? get owner => switch (ownerState) {
        OwnerState.owner => true,
        OwnerState.notOwner => false,
        _ => null,
      };

  bool get ready => setup?.ready ?? false;

  /// The chosen project already has a queued or coding request.
  bool get activeExists =>
      projectId != null && busyProjects(items).contains(projectId);

  List<DevProject> get sendable => sendableProjects(projects);
  DevProject? get selectedProject {
    for (final project in projects) {
      if (project.id == projectId) return project;
    }
    return null;
  }

  void selectProject(String? id) {
    projectId = id;
    _notify();
  }

  DevRequest? find(String id) {
    for (final item in items) {
      if (item.id == id) return item;
    }
    return null;
  }

  /// The merge approval to show for [item]: the one opened here, else the
  /// pending one the API returned, unless dismissed.
  WorkspaceApproval? reviewFor(DevRequest item) {
    if (_dismissed.contains(item.id) ||
        item.status != DevStatus.waitingApproval) {
      return null;
    }
    return _reviews[item.id] ?? item.approval;
  }

  /// Loads the caller's role once per workspace; true for an active owner.
  /// After a workspace switch everything shown here is forgotten first.
  Future<bool> ensureOwner() async {
    String? workspace;
    try {
      workspace = await repository.workspaceId();
    } on Object {
      workspace = _workspaceId;
    }
    if (_disposed) return false;
    if (workspace != _workspaceId && ownerState != OwnerState.loading) {
      if (ownerState != OwnerState.unknown) reset();
      _workspaceId = workspace;
    }
    if (ownerState == OwnerState.owner) return true;
    if (ownerState == OwnerState.notOwner) return false;
    return _roleLoad ??= _loadRole();
  }

  Future<bool> _loadRole() async {
    ownerState = OwnerState.loading;
    roleError = null;
    _notify();
    try {
      role = await repository.currentRole();
      if (_disposed) return false;
      ownerState = role == 'owner' ? OwnerState.owner : OwnerState.notOwner;
    } on Object catch (error) {
      if (error is ApiException && error.isPermissionDenied) {
        ownerState = OwnerState.notOwner;
      } else {
        ownerState = OwnerState.error;
        roleError =
            l.f('dev.n.roleError', {'reason': explainDeveloperError(error)});
      }
    } finally {
      _roleLoad = null;
    }
    _notify();
    if (ownerState == OwnerState.owner &&
        _active &&
        phase == DevPhase.initial) {
      unawaited(load());
    }
    return ownerState == OwnerState.owner;
  }

  /// Checks the role again (a failed check, or a different workspace).
  Future<bool> reloadRole() {
    if (ownerState != OwnerState.loading) ownerState = OwnerState.unknown;
    return ensureOwner();
  }

  /// Forgets everything (sign-out or a workspace switch).
  void reset() {
    _poll?.cancel();
    _loadToken++;
    ownerState = OwnerState.unknown;
    role = null;
    roleError = null;
    phase = DevPhase.initial;
    refreshing = false;
    loadError = null;
    setup = null;
    items = const [];
    projects = const [];
    projectId = null;
    _lastProject = null;
    hasMore = false;
    query = '';
    busy = null;
    notice = null;
    budget = null;
    _reviews.clear();
    _dismissed.clear();
    _notify();
  }

  /// The screen is on screen: load once and refresh while something moves.
  void setActive(bool value) {
    if (_active == value) return;
    _active = value;
    if (!value) {
      _poll?.cancel();
      return;
    }
    unawaited(() async {
      if (!await ensureOwner() || _disposed || !_active) return;
      if (phase == DevPhase.initial) {
        await load();
      } else {
        _schedulePoll();
      }
    }());
  }

  /// Loads the setup and the first page; false when that failed.
  Future<bool> load() async {
    final token = ++_loadToken;
    _poll?.cancel();
    // A reload keeps the list on screen with a progress bar.
    if (phase != DevPhase.ready) phase = DevPhase.loading;
    refreshing = true;
    loadError = null;
    var ok = false;
    _notify();
    try {
      final results = await Future.wait<Object>([
        repository.setup(),
        repository.list(limit: pageSize, query: query),
        _projectsOrEmpty(),
      ]);
      if (_disposed || token != _loadToken) return false;
      refreshing = false;
      setup = results[0] as DevSetup;
      final list = results[1] as List<DevRequest>;
      _setProjects(results[2] as List<DevProject>);
      items = list;
      hasMore = list.length == pageSize;
      phase = DevPhase.ready;
      ok = true;
    } on Object catch (error) {
      if (_disposed || token != _loadToken) return false;
      refreshing = false;
      if (error is ApiException && error.isPermissionDenied) {
        phase = DevPhase.forbidden;
        loadError = explainDeveloperError(error);
      } else if (phase == DevPhase.ready) {
        // A failed refresh keeps the last known list on screen.
        _fail(l.f(
            'dev.n.refreshFailed', {'reason': explainDeveloperError(error)}));
      } else {
        phase = DevPhase.error;
        loadError =
            l.f('dev.n.loadFailed', {'reason': explainDeveloperError(error)});
      }
    }
    _notify();
    _schedulePoll();
    return ok;
  }

  /// Not set up, or not enabled here: the setup card explains it and the
  /// list is simply empty.
  Future<List<DevProject>> _projectsOrEmpty() async {
    try {
      return await repository.projects();
    } on ApiException catch (error) {
      if (error.statusCode == 409 || error.statusCode == 403) return const [];
      rethrow;
    }
  }

  void _setProjects(List<DevProject> list) {
    projects = list;
    if (projectId == null ||
        !sendableProjects(list).any((p) => p.id == projectId)) {
      projectId = defaultProjectId(list, _lastProject);
    }
  }

  /// Reads the projects and the setup again (after a project changed).
  Future<void> reloadProjects() async {
    final results =
        await Future.wait<Object>([repository.setup(), _projectsOrEmpty()]);
    if (_disposed) return;
    setup = results[0] as DevSetup;
    _setProjects(results[1] as List<DevProject>);
    _notify();
  }

  DevProject? findProject(String id) {
    for (final project in projects) {
      if (project.id == id) return project;
    }
    return null;
  }

  /// Adds a project or saves changes to one. Returns the error to show next
  /// to the form, or null when it was saved.
  Future<String?> saveProject(ProjectDraft draft,
      {DevProject? existing}) async {
    final payload = projectPayload(draft);
    if (payload is InvalidProject) return payload.error;
    final body = (payload as ValidProject).body;
    busy = 'project-save';
    notice = null;
    _notify();
    try {
      final saved = existing == null
          ? await repository.createProject(body)
          : await repository.updateProject(existing.id, body);
      await reloadProjects();
      noticeFailed = false;
      notice = l.f(existing == null ? 'dev.n.added' : 'dev.n.saved',
          {'name': saved.name});
      return null;
    } on Object catch (error) {
      return error is ApiException && error.isPermissionDenied
          ? l.t('dev.n.projectsOwnerOnly')
          : explainDeveloperError(error);
    } finally {
      busy = null;
      _notify();
    }
  }

  Future<void> testProject(DevProject project) =>
      _act('project-test:${project.id}', () async {
        final tested = await repository.testProject(project.id);
        await reloadProjects();
        final check = tested.lastCheck;
        noticeFailed = !(check?.ok ?? false);
        notice = '${project.name}: ${check?.message ?? 'tested.'}';
      });

  Future<void> installWorkflow(DevProject project) =>
      _act('project-install:${project.id}', () async {
        final result = await repository.installWorkflow(project.id);
        await reloadProjects();
        noticeFailed = false;
        notice = l.f(
            result.created ? 'dev.n.workflowOpened' : 'dev.n.workflowWaiting',
            {'number': result.number, 'repository': project.repository});
      });

  Future<void> toggleProject(DevProject project) =>
      _act('project-toggle:${project.id}', () async {
        await repository
            .updateProject(project.id, {'enabled': !project.enabled});
        await reloadProjects();
        noticeFailed = false;
        notice = project.enabled
            ? '${project.name} is turned off: it takes no requests and no '
                'merges.'
            : '${project.name} is turned on.';
      });

  /// The screen asks first.
  Future<void> deleteProject(DevProject project) =>
      _act('project-delete:${project.id}', () async {
        await repository.deleteProject(project.id);
        await reloadProjects();
        noticeFailed = false;
        notice = l.f('dev.n.deleted', {'name': project.name});
      });

  void _schedulePoll() {
    _poll?.cancel();
    if (!_active || _disposed || phase != DevPhase.ready) return;
    final delay = pollDelay(items);
    if (delay == null) return;
    _poll = Timer(delay, () {
      if (_active && !_disposed && busy == null) unawaited(load());
    });
  }

  Future<void> search(String value) async {
    final next = value.trim();
    if (next == query && phase == DevPhase.ready) return;
    final previous = query;
    query = next;
    // A search that failed leaves the list it had, under its own search.
    if (!await load() && phase == DevPhase.ready) {
      query = previous;
      _notify();
    }
  }

  Future<void> clearSearch() => search('');

  Future<void> loadMore() async {
    if (items.isEmpty || busy != null) return;
    busy = 'more';
    notice = null;
    _notify();
    try {
      final page = await repository.list(
          limit: pageSize, before: items.last.id, query: query);
      if (_disposed) return;
      items = [
        ...items,
        for (final item in page)
          if (find(item.id) == null) item
      ];
      hasMore = page.length == pageSize;
    } on Object catch (error) {
      _fail(l.f('dev.n.moreFailed', {'reason': explainDeveloperError(error)}));
    } finally {
      busy = null;
      _notify();
    }
  }

  /// Validates and sends a new request. Returns true when it was created.
  Future<bool> create(String text, {bool allowProtected = false}) async {
    final project = selectedProject;
    if (project == null) {
      _fail(l.t('dev.n.chooseProject'));
      _notify();
      return false;
    }
    final body = requestBody(text, allowProtected: allowProtected);
    if (body is InvalidRequest) {
      _fail(body.error);
      _notify();
      return false;
    }
    final valid = body as ValidRequest;
    busy = 'create';
    notice = null;
    budget = null;
    _notify();
    try {
      final created = await repository.create(valid.text,
          source: 'text',
          allowProtectedPaths: valid.allowProtectedPaths,
          projectId: project.id);
      if (_disposed) return true;
      _lastProject = project.id;
      items = [
        created,
        for (final item in items)
          if (item.id != created.id) item
      ];
      setup = setup?.withOneMore();
      noticeFailed = false;
      notice =
          l.f('dev.n.sent', {'project': created.projectName ?? project.name});
      return true;
    } on Object catch (error) {
      if (error is ApiException && error.isBudgetExceeded) {
        budget = error.message;
      } else {
        _fail(l.f('dev.n.notSent', {'reason': explainDeveloperError(error)}));
      }
      return false;
    } finally {
      busy = null;
      _notify();
      _schedulePoll();
    }
  }

  void _replace(DevRequest next) {
    items = [for (final item in items) item.id == next.id ? next : item];
  }

  Future<void> _act(String key, Future<void> Function() action) async {
    if (busy != null) return;
    busy = key;
    notice = null;
    _notify();
    try {
      await action();
    } on Object catch (error) {
      _fail(error is ApiException && error.isPermissionDenied
          ? l.f('dev.n.refused', {'reason': explainDeveloperError(error)})
          : explainDeveloperError(error));
    } finally {
      busy = null;
      _notify();
      _schedulePoll();
    }
  }

  /// "Check now": the request refreshed from GitHub.
  Future<void> refresh(DevRequest item) => _act('refresh:${item.id}', () async {
        _replace(await repository.get(item.id, refresh: true));
      });

  /// Cancels the run, closes the pull request and deletes the branch. The
  /// screen asks first.
  Future<void> cancel(DevRequest item) => _act('cancel:${item.id}', () async {
        _replace(await repository.cancel(item.id));
        noticeFailed = false;
        notice =
            l.f('dev.n.cancelled', {'text': shortText(item.text, limit: 60)});
      });

  /// "Approve & merge": opens the merge approval with its payload hash. It
  /// merges nothing; [merge] does, after an explicit Confirm merge.
  Future<void> review(DevRequest item) => _act('review:${item.id}', () async {
        final result = await repository.requestMerge(item.id);
        _replace(result.request);
        _dismissed.remove(item.id);
        _reviews[item.id] = result.approval;
      });

  void dismissReview(DevRequest item) {
    _dismissed.add(item.id);
    _notify();
  }

  /// Confirm merge: approves the displayed approval with its payload hash,
  /// then reads the request again.
  Future<void> merge(DevRequest item, WorkspaceApproval approval) =>
      _act('merge:${item.id}', () async {
        if (approval.payloadHash == null) {
          _fail(l.t('dev.n.unbound'));
          return;
        }
        String? result;
        try {
          result = await repository.approve(approval);
        } on ApiException catch (error) {
          _fail(error.isPermissionDenied
              ? l.f('dev.n.mergeRefused', {'reason': error.message})
              : l.f(
                  'dev.n.notMerged', {'reason': explainDeveloperError(error)}));
          return;
        }
        _reviews.remove(item.id);
        final updated = await repository.get(item.id, refresh: true);
        _replace(updated);
        if (updated.status == DevStatus.merged) {
          noticeFailed = false;
          notice = l.f('dev.n.merged', {'number': updated.prNumber ?? ''});
        } else {
          _fail(updated.mergeError ?? result ?? l.t('dev.n.noMerge'));
        }
      });

  void dismissNotice() {
    notice = null;
    _notify();
  }

  void dismissBudget() {
    budget = null;
    _notify();
  }

  void _fail(String message) {
    noticeFailed = true;
    notice = message;
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
