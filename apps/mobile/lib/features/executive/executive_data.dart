import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';
import '../../data/session_store.dart';

/// The executive desk on the phone (docs/ANUM_MINE_EXECUTIVE_PA.md#mobile):
/// the workspaces the person may see, each kept apart, the grants of the
/// current workspace and today's briefing. Numbers come only from the
/// grant-checked `GET /api/v1/executive/overview`; a workspace without a
/// grant shows nothing and opens only by an explicit workspace switch.

/// What a grant may share (`ExecutiveScope` in packages/contracts).
const executiveScopes = ['counts', 'tasks', 'approvals', 'cost', 'meetings'];

/// The figures of one workspace summary. A scope that was not granted is
/// null, never 0 (`summaryFigures` on the web).
class ExecutiveFigures {
  const ExecutiveFigures(
      {this.tasks,
      this.blocked,
      this.approvals,
      this.costUsd,
      this.meetings,
      this.agents,
      this.rooms});
  final int? tasks, blocked, approvals, meetings, agents, rooms;
  final String? costUsd;

  factory ExecutiveFigures.fromJson(JsonReader? s) {
    if (s == null) return const ExecutiveFigures();
    final tasks = s.optObject('tasks');
    final by = tasks?.optMap('by_status');
    int? total;
    if (by != null) {
      total = 0;
      for (final value in by.values) {
        if (value is num && value.isFinite) total = total! + value.toInt();
      }
    }
    final counts = s.optObject('counts');
    final cost = s.optObject('cost');
    return ExecutiveFigures(
      tasks: total,
      blocked:
          by == null ? null : ((by['waiting_approval'] as num?)?.toInt() ?? 0),
      approvals: s.optObject('approvals')?.optInt('pending'),
      costUsd: cost?.optString('estimated_cost_usd'),
      meetings: s.optObject('meetings')?.optInt('active'),
      agents: counts?.optInt('agents'),
      rooms: counts?.optInt('rooms'),
    );
  }

  bool get isEmpty =>
      tasks == null &&
      blocked == null &&
      approvals == null &&
      costUsd == null &&
      meetings == null &&
      agents == null;
}

enum ExecutiveAccess { granted, notGranted, unavailable }

class ExecutiveWorkspace {
  const ExecutiveWorkspace(
      {required this.workspaceId,
      required this.access,
      this.name,
      this.reason,
      this.canSwitch = false,
      this.figures = const ExecutiveFigures(),
      this.asOf,
      this.expiresAt});
  final String workspaceId;
  final String? name, reason;
  final ExecutiveAccess access;
  final bool canSwitch;
  final ExecutiveFigures figures;
  final DateTime? asOf, expiresAt;

  String get label => name ?? workspaceId;

  factory ExecutiveWorkspace.fromJson(JsonReader j) {
    final access = switch (j.string('access')) {
      'granted' => ExecutiveAccess.granted,
      'not_granted' => ExecutiveAccess.notGranted,
      _ => ExecutiveAccess.unavailable,
    };
    // A workspace that is not granted must carry no summary: refuse a server
    // that leaks one, as the web does.
    if (access != ExecutiveAccess.granted && j.json.containsKey('summary')) {
      throw const FormatException(
          'The overview leaked data for a workspace without a grant.');
    }
    return ExecutiveWorkspace(
      workspaceId: j.string('workspace_id'),
      access: access,
      name: j.optString('name'),
      reason: j.optString('reason'),
      canSwitch: j.optString('open_with') == 'workspace_switch' ||
          access == ExecutiveAccess.granted,
      figures: access == ExecutiveAccess.granted
          ? ExecutiveFigures.fromJson(j.optObject('summary'))
          : const ExecutiveFigures(),
      asOf: j.optDate('as_of'),
      expiresAt: j.optDate('expires_at'),
    );
  }
}

class ExecutiveOverview {
  const ExecutiveOverview({required this.workspaces, this.truncated = false});
  final List<ExecutiveWorkspace> workspaces;
  final bool truncated;

  factory ExecutiveOverview.fromJson(JsonReader j) {
    if (j.optInt('version') != 1) {
      throw const FormatException('The overview is not version 1.');
    }
    final items = j.objects('workspaces', ExecutiveWorkspace.fromJson);
    final seen = <String>{};
    for (final w in items) {
      if (!seen.add(w.workspaceId)) {
        throw const FormatException('The overview repeated a workspace.');
      }
    }
    return ExecutiveOverview(
        workspaces: items, truncated: j.optBool('truncated') ?? false);
  }

  List<ExecutiveWorkspace> of(ExecutiveAccess access) => [
        for (final w in workspaces)
          if (w.access == access) w
      ];
}

class ExecutiveGrant {
  const ExecutiveGrant(
      {required this.id,
      required this.granteeUserId,
      required this.scopes,
      required this.status,
      required this.payloadHash,
      required this.revision,
      required this.requestedBy,
      this.requestedAt,
      this.expiresAt});
  final String id, granteeUserId, status, payloadHash, requestedBy;
  final List<String> scopes;
  final int revision;
  final DateTime? requestedAt, expiresAt;

  factory ExecutiveGrant.fromJson(JsonReader j) => ExecutiveGrant(
        id: j.string('id'),
        granteeUserId: j.string('grantee_user_id'),
        scopes: j.strings('scopes'),
        status: j.string('status'),
        payloadHash: j.optString('payload_hash') ?? '',
        revision: j.optInt('revision') ?? 0,
        requestedBy: j.optString('requested_by') ?? '',
        requestedAt: j.optDate('requested_at'),
        expiresAt: j.optDate('expires_at'),
      );
}

/// One item of a briefing section; the server writes it in the asked
/// language (`detail_text`).
class BriefingItem {
  const BriefingItem({required this.label, this.detail});
  final String label;
  final String? detail;
}

class BriefingSection {
  const BriefingSection(
      {required this.key,
      required this.title,
      required this.headline,
      required this.state,
      required this.items,
      this.count,
      this.truncated = false,
      this.notes = const []});
  final String key, title, headline, state;
  final int? count;
  final bool truncated;
  final List<BriefingItem> items;
  final List<String> notes;

  factory BriefingSection.fromJson(JsonReader j) => BriefingSection(
        key: j.optString('key') ?? '',
        title: j.optString('title') ?? '',
        headline: j.optString('headline') ?? '',
        state: j.optString('state') ?? 'ok',
        count: j.optInt('count'),
        truncated: j.optBool('truncated') ?? false,
        items: j.optObjects(
            'items',
            (i) => BriefingItem(
                label: i.optString('label') ?? '',
                detail: i.optString('detail_text') ?? i.optString('detail'))),
        notes: j.optStrings('note_texts') ?? const [],
      );
}

class Briefing {
  const Briefing({required this.title, required this.sections});
  final String title;
  final List<BriefingSection> sections;

  factory Briefing.fromJson(JsonReader j) => Briefing(
      title: j.optString('title') ?? '',
      sections: j.optObjects('sections', BriefingSection.fromJson));
}

abstract interface class ExecutiveRepository {
  Future<ExecutiveOverview> overview();

  /// The current workspace's own summary through the assistant's read tool;
  /// null when it is not available.
  Future<ExecutiveFigures?> ownSummary();
  Future<Briefing> briefing(String lang);
  Future<List<ExecutiveGrant>> grants();
  Future<void> createGrant(
      {required String grantee,
      required List<String> scopes,
      DateTime? expiresAt});
  Future<void> approveGrant(ExecutiveGrant grant);
  Future<void> rejectGrant(ExecutiveGrant grant);
  Future<void> narrowGrant(ExecutiveGrant grant, List<String> scopes);
  Future<void> revokeGrant(ExecutiveGrant grant);

  /// The workspace the app acts in now.
  Future<String?> currentWorkspaceId();
}

final _grantId = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$');
final _hash = RegExp(r'^[0-9a-f]{64}$');

class ApiExecutiveRepository implements ExecutiveRepository {
  const ApiExecutiveRepository(this.api, this.sessions);
  final AnumApiClient api;
  final SessionStore sessions;
  static const _root = '/api/v1/executive';

  String _grant(String id) {
    if (!_grantId.hasMatch(id)) {
      throw const ApiException(0, 'Invalid grant identifier.');
    }
    return '$_root/grants/${Uri.encodeComponent(id)}';
  }

  @override
  Future<String?> currentWorkspaceId() async =>
      (await sessions.read())?.context.workspaceId;

  @override
  Future<ExecutiveOverview> overview() async {
    final started = await sessions.read();
    final body = JsonReader(await api.request('GET', '$_root/overview'));
    final tenant = body.optString('tenant_id');
    if (started != null && tenant != started.context.tenantId) {
      throw const FormatException(
          'The overview belongs to another organisation.');
    }
    final now = await sessions.read();
    if (now?.context.workspaceId != started?.context.workspaceId) {
      throw const FormatException('The workspace changed while loading.');
    }
    return ExecutiveOverview.fromJson(body);
  }

  @override
  Future<ExecutiveFigures?> ownSummary() async {
    try {
      final body = JsonReader(
          await api.request('POST', '/api/v1/executive-pa/invoke', body: {
        'tool': 'summarise_workspace',
        'arguments': <String, Object?>{},
        'input_mode': 'typed',
      }));
      final summary = body.optObject('summary');
      return summary == null ? null : ExecutiveFigures.fromJson(summary);
    } on Object {
      return null;
    }
  }

  @override
  Future<Briefing> briefing(String lang) async => Briefing.fromJson(JsonReader(
      await api.request('GET', '/api/v1/executive-pa/briefing?lang=$lang')));

  @override
  Future<List<ExecutiveGrant>> grants() async =>
      JsonReader(await api.request('GET', '$_root/grants?limit=100'))
          .optObjects('items', ExecutiveGrant.fromJson);

  @override
  Future<void> createGrant(
      {required String grantee,
      required List<String> scopes,
      DateTime? expiresAt}) async {
    await api.request('POST', '$_root/grants', body: {
      'grantee_user_id': grantee,
      'scopes': scopes,
      if (expiresAt != null) 'expires_at': expiresAt.toUtc().toIso8601String(),
    });
  }

  @override
  Future<void> approveGrant(ExecutiveGrant grant) async {
    if (!_hash.hasMatch(grant.payloadHash)) {
      throw const ApiException(
          0, 'A 64 character payload hash is required to approve.');
    }
    await api.request('POST', '${_grant(grant.id)}/approve',
        body: {'payload_hash': grant.payloadHash});
  }

  @override
  Future<void> rejectGrant(ExecutiveGrant grant) async {
    await api.request('POST', '${_grant(grant.id)}/reject', body: {});
  }

  @override
  Future<void> narrowGrant(ExecutiveGrant grant, List<String> scopes) async {
    await api.request('PATCH', _grant(grant.id),
        body: {'expected_revision': grant.revision, 'scopes': scopes});
  }

  @override
  Future<void> revokeGrant(ExecutiveGrant grant) async {
    await api.request('DELETE', _grant(grant.id));
  }
}

enum ExecutivePhase { loading, ready, denied, notConnected, error }

enum GrantsPhase { loading, ready, denied, error }

/// A short message for the screen: a catalog key with values, failed or not.
class DeskNote {
  const DeskNote(this.key, {this.values = const {}, this.failed = false});
  final String key;
  final Map<String, Object> values;
  final bool failed;
}

String _message(Object error) =>
    error is ApiException ? error.readable : readableError(error);

class ExecutiveController extends ChangeNotifier {
  ExecutiveController(this.repository,
      {this.switchWorkspace, this.language = 'en'});
  final ExecutiveRepository repository;

  /// Switches the app to another workspace (the API confirms the
  /// membership); null where the app cannot switch from here.
  final Future<void> Function(String workspaceId)? switchWorkspace;
  String language;

  ExecutivePhase phase = ExecutivePhase.loading;
  String? error;
  ExecutiveOverview? overview;
  ExecutiveFigures? own;
  String? currentWorkspaceId;
  DeskNote? note;
  String? switching;

  GrantsPhase grantsPhase = GrantsPhase.loading;
  String? grantsError;
  List<ExecutiveGrant> grantList = const [];
  bool grantBusy = false;
  DeskNote? grantNote;

  bool briefingLoading = true;
  String? briefingError;
  Briefing? briefing;

  Future<void> load() async {
    phase = ExecutivePhase.loading;
    error = null;
    notifyListeners();
    currentWorkspaceId = await repository.currentWorkspaceId();
    try {
      final values = await Future.wait<Object?>(
          [repository.overview(), repository.ownSummary()]);
      overview = values[0] as ExecutiveOverview;
      own = values[1] as ExecutiveFigures?;
      phase = ExecutivePhase.ready;
    } on ApiException catch (e) {
      if (e.isPermissionDenied) {
        phase = ExecutivePhase.denied;
      } else if (e.statusCode == 503) {
        phase = ExecutivePhase.notConnected;
        own = await repository.ownSummary();
      } else {
        phase = ExecutivePhase.error;
        error = e.readable;
      }
    } on Object catch (e) {
      phase = ExecutivePhase.error;
      error = _message(e);
    }
    notifyListeners();
    await Future.wait([loadGrants(), loadBriefing()]);
  }

  Future<void> loadGrants() async {
    grantsPhase = GrantsPhase.loading;
    grantsError = null;
    notifyListeners();
    try {
      grantList = await repository.grants();
      grantsPhase = GrantsPhase.ready;
    } on ApiException catch (e) {
      grantsPhase =
          e.isPermissionDenied ? GrantsPhase.denied : GrantsPhase.error;
      grantsError = e.readable;
    } on Object catch (e) {
      grantsPhase = GrantsPhase.error;
      grantsError = _message(e);
    }
    notifyListeners();
  }

  Future<void> loadBriefing() async {
    briefingLoading = true;
    briefingError = null;
    notifyListeners();
    try {
      briefing = await repository.briefing(language == 'ar' ? 'ar' : 'en');
    } on Object catch (e) {
      briefingError = _message(e);
    }
    briefingLoading = false;
    notifyListeners();
  }

  Future<void> openWorkspace(ExecutiveWorkspace w) async {
    final change = switchWorkspace;
    if (change == null || switching != null) return;
    switching = w.workspaceId;
    note = const DeskNote('executive.switching');
    notifyListeners();
    try {
      await change(w.workspaceId);
      note = DeskNote('executive.switchedTo', values: {'name': w.label});
      switching = null;
      await load();
      return;
    } on Object catch (e) {
      note = DeskNote('executive.switchFailed',
          values: {'message': _message(e)}, failed: true);
    }
    switching = null;
    notifyListeners();
  }

  Future<bool> _grantAction(Future<void> Function() work, String done) async {
    if (grantBusy) return false;
    grantBusy = true;
    grantNote = null;
    notifyListeners();
    var ok = false;
    try {
      await work();
      grantNote = DeskNote(done);
      ok = true;
    } on ApiException catch (e) {
      grantNote = e.isPermissionDenied
          ? const DeskNote('executive.grants.denied', failed: true)
          : DeskNote('executive.grants.failed',
              values: {'message': e.readable}, failed: true);
    } on Object catch (e) {
      grantNote = DeskNote('executive.grants.failed',
          values: {'message': _message(e)}, failed: true);
    }
    grantBusy = false;
    notifyListeners();
    if (ok) await loadGrants();
    return ok;
  }

  /// Requests a grant; it gives no access until an owner approves its hash.
  Future<bool> request(
      String grantee, List<String> scopes, DateTime? expiresAt) {
    final who = grantee.trim();
    if (who.isEmpty || who.length > 160 || scopes.isEmpty) {
      grantNote = const DeskNote('executive.pickOne', failed: true);
      notifyListeners();
      return Future.value(false);
    }
    return _grantAction(
        () => repository.createGrant(
            grantee: who, scopes: scopes, expiresAt: expiresAt),
        'executive.grants.requested');
  }

  Future<bool> approve(ExecutiveGrant g) => _grantAction(
      () => repository.approveGrant(g), 'executive.grants.approved');
  Future<bool> reject(ExecutiveGrant g) => _grantAction(
      () => repository.rejectGrant(g), 'executive.grants.rejected');
  Future<bool> narrow(ExecutiveGrant g, List<String> scopes) => _grantAction(
      () => repository.narrowGrant(g, scopes), 'executive.grants.narrowed');
  Future<bool> revoke(ExecutiveGrant g) =>
      _grantAction(() => repository.revokeGrant(g), 'executive.grants.revoked');
}
