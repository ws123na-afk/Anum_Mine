import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'org_admin_models.dart';

/// Organization admin data: the contact suppression list, this workspace's
/// lifecycle, setup plans and the org chart. The API decides who may do
/// what; the screens show its `403` instead of guessing roles.
abstract interface class OrgAdminRepository {
  /// One page of the suppression list (owners and organization
  /// administrators; everyone else gets 403).
  Future<SuppressionPage> suppressions(
      {String query = '', String? channel, int offset = 0, int limit = 50});

  /// Adds an entry; `created` is false when it was already on the list.
  Future<({SuppressionEntry entry, bool created})> addSuppression(
      SuppressionDraft draft);

  /// Removes an entry; the reason is kept in the audit log.
  Future<void> removeSuppression(String id, String reason);

  Future<WorkspaceInfo> currentWorkspace();
  Future<WorkspacePermissions> workspacePermissions();
  Future<WorkspaceInfo> renameWorkspace(String name);
  Future<WorkspaceInfo> archiveWorkspace(String confirmName);
  Future<WorkspaceInfo> restoreWorkspace();

  Future<List<SetupPlanSummary>> setupPlans();

  /// What applying [id] in [phase] would do here. Read-only.
  Future<SetupPlanPreview> setupPlanPreview(String id, {String phase});

  /// Agents, department assistants and people, for the read-only org chart.
  Future<OrgChartData> orgChart();
}

class ApiOrgAdminRepository implements OrgAdminRepository {
  const ApiOrgAdminRepository(this.api);
  final AnumApiClient api;

  static const _suppressions = '/api/v1/contact-suppressions';
  static const _workspace = '/api/v1/workspaces/current';
  static const _plans = '/api/v1/setup-plans';

  /// Pages of agents read for the chart (100 each).
  static const agentPages = 5;

  @override
  Future<SuppressionPage> suppressions(
      {String query = '',
      String? channel,
      int offset = 0,
      int limit = 50}) async {
    final path = Uri(path: _suppressions, queryParameters: {
      'limit': '$limit',
      'offset': '$offset',
      if (query.trim().isNotEmpty) 'q': query.trim(),
      if (channel != null && channel.isNotEmpty) 'channel': channel,
    }).toString();
    return SuppressionPage.fromJson(JsonReader(await api.request('GET', path)));
  }

  @override
  Future<({SuppressionEntry entry, bool created})> addSuppression(
      SuppressionDraft draft) async {
    final result = await api.requestWithStatus('POST', _suppressions,
        body: draft.toJson());
    return (
      entry: SuppressionEntry.fromJson(JsonReader(result.body)),
      created: result.status == 201,
    );
  }

  @override
  Future<void> removeSuppression(String id, String reason) async {
    await api.request('DELETE', '$_suppressions/${Uri.encodeComponent(id)}',
        body: {'reason': reason});
  }

  @override
  Future<WorkspaceInfo> currentWorkspace() async =>
      WorkspaceInfo.fromJson(JsonReader(await api.request('GET', _workspace)));

  @override
  Future<WorkspacePermissions> workspacePermissions() async =>
      WorkspacePermissions.fromJson(
          JsonReader(await api.request('GET', '$_workspace/permissions')));

  @override
  Future<WorkspaceInfo> renameWorkspace(String name) async =>
      WorkspaceInfo.fromJson(JsonReader(
          await api.request('PATCH', _workspace, body: {'name': name})));

  @override
  Future<WorkspaceInfo> archiveWorkspace(String confirmName) async =>
      WorkspaceInfo.fromJson(JsonReader(await api.request(
          'POST', '$_workspace/archive',
          body: {'confirm_name': confirmName})));

  @override
  Future<WorkspaceInfo> restoreWorkspace() async =>
      WorkspaceInfo.fromJson(JsonReader(
          await api.request('POST', '$_workspace/restore', body: const {})));

  @override
  Future<List<SetupPlanSummary>> setupPlans() async =>
      JsonReader(await api.request('GET', _plans))
          .optObjects('data', SetupPlanSummary.fromJson);

  @override
  Future<SetupPlanPreview> setupPlanPreview(String id,
          {String phase = 'pilot'}) async =>
      SetupPlanPreview.fromJson(JsonReader(await api.request('GET',
          '$_plans/${Uri.encodeComponent(id)}/preview?phase=$phase&overwrite_edited=false')));

  @override
  Future<OrgChartData> orgChart() async {
    // Agents are required (a 403 here is the screen's permission state);
    // assistants and people are optional, as on the web chart.
    final members = <OrgMember>[];
    String? cursor;
    var pages = 0;
    do {
      final path = Uri(path: '/api/v1/agents', queryParameters: {
        'limit': '100',
        if (cursor != null) 'cursor': cursor,
      }).toString();
      final page = JsonReader(await api.request('GET', path));
      for (final agent in page.optObjects('items', (r) => r)) {
        members.add(OrgMember(
          kind: OrgMemberKind.agent,
          id: agent.string('id'),
          name: agent.string('name'),
          description: agent.optString('description') ?? '',
          department: agent.optString('department'),
          active: (agent.optString('status') ?? 'active') != 'paused',
          attention: (agent.optStrings('unavailable_tools') ?? const [])
                  .isNotEmpty ||
              (agent.optStrings('unavailable_skills') ?? const []).isNotEmpty,
        ));
      }
      cursor = page.optString('next_cursor');
      pages += 1;
    } while (cursor != null && pages < agentPages);

    var assistantsFailed = false;
    try {
      final body = JsonReader(await api.request('GET', '/api/v1/assistants'));
      for (final a in body.optObjects('data', (r) => r)) {
        final department = a.optString('department') ?? 'custom';
        members.add(OrgMember(
          kind: OrgMemberKind.assistant,
          id: a.string('id'),
          name: a.string('name'),
          // A custom assistant sits in the department its name says.
          department: department == 'custom' ? a.string('name') : department,
          active: a.optBool('enabled') ?? true,
        ));
      }
    } on ApiException {
      assistantsFailed = true;
    }

    var peopleFailed = false;
    try {
      final body = JsonReader(await api.request('GET', '/api/v1/team/people'));
      for (final p in body.optObjects('data', (r) => r)) {
        final department = p.optString('department');
        if (department == null || department.isEmpty) continue;
        members.add(OrgMember(
          kind: OrgMemberKind.person,
          id: p.string('user_id'),
          name: p.string('display_name'),
          department: department,
          jobTitle: p.optString('job_title'),
          role: p.optString('role'),
        ));
      }
    } on ApiException {
      peopleFailed = true;
    }
    return OrgChartData(
        members: members,
        assistantsFailed: assistantsFailed,
        peopleFailed: peopleFailed,
        truncated: cursor != null);
  }
}
