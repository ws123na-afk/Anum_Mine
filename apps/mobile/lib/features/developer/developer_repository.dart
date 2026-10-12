import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';
import 'developer_models.dart';

/// The merge approval the API created for a request, with the request as it
/// is now.
class MergeReview {
  const MergeReview({required this.request, required this.approval});
  final DevRequest request;
  final WorkspaceApproval approval;
}

/// The pull request Install workflow opened (or found already open).
class WorkflowInstall {
  const WorkflowInstall(
      {required this.project,
      required this.number,
      required this.url,
      required this.created});
  final DevProject project;
  final int number;
  final String url;
  final bool created;
}

/// Developer mode over the API (docs/developer-mode.md). Every
/// `/api/v1/developer` route is owner-only on the server.
abstract interface class DeveloperRepository {
  Future<DevSetup> setup();
  Future<List<DevRequest>> list(
      {int limit = 20, String? before, String? query, String? projectId});
  Future<DevRequest> get(String id, {bool refresh = false});
  Future<DevRequest> create(String text,
      {required String source,
      bool allowProtectedPaths = false,
      String? projectId});
  Future<DevRequest> cancel(String id);

  /// The workspace's projects (GitHub repositories), by name.
  Future<List<DevProject>> projects();

  /// [body] may carry a token once; the answer never contains it.
  Future<DevProject> createProject(JsonMap body);
  Future<DevProject> updateProject(String id, JsonMap body);
  Future<void> deleteProject(String id);

  /// Test connection: reachable with the token, workflow on the branch.
  Future<DevProject> testProject(String id);

  /// Opens (or finds) the pull request adding the workflow; the owner
  /// merges it in GitHub.
  Future<WorkflowInstall> installWorkflow(String id);

  /// Which project a chat or voice request is for. Creates nothing.
  Future<Resolution> resolve(String text);

  /// Asks for the merge: the API answers with the pending approval to show
  /// (tool, arguments, payload hash).
  Future<MergeReview> requestMerge(String id);

  /// Approves through the approvals flow with the displayed payload hash
  /// (`POST /api/v1/approvals/{id}/approve {"payload_hash": ...}`). Returns
  /// the run's result sentence when the API sends one.
  Future<String?> approve(WorkspaceApproval approval);

  /// The caller's role in this workspace, or null when the membership is
  /// inactive or unknown.
  Future<String?> currentRole();

  /// The signed-in workspace, to notice a switch.
  Future<String?> workspaceId();
}

class ApiDeveloperRepository implements DeveloperRepository {
  const ApiDeveloperRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/developer';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<DevSetup> setup() async =>
      DevSetup.fromJson(JsonReader(await api.request('GET', '$_base/setup')));

  @override
  Future<List<DevRequest>> list(
      {int limit = 20,
      String? before,
      String? query,
      String? projectId}) async {
    final params = <String, String>{
      'limit': '$limit',
      if (before != null) 'before': before,
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
      if (projectId != null && projectId.isNotEmpty) 'project_id': projectId,
    };
    final path = Uri(path: '$_base/requests', queryParameters: params);
    // The API answers with a JSON array; the transport wraps it as `data`.
    return JsonReader(await api.request('GET', path.toString()))
        .objects('data', DevRequest.fromJson);
  }

  @override
  Future<DevRequest> get(String id, {bool refresh = false}) async =>
      DevRequest.fromJson(JsonReader(await api.request('GET',
          '$_base/requests/${_id(id)}${refresh ? '?refresh=true' : ''}')));

  @override
  Future<DevRequest> create(String text,
          {required String source,
          bool allowProtectedPaths = false,
          String? projectId}) async =>
      DevRequest.fromJson(
          JsonReader(await api.request('POST', '$_base/requests', body: {
        'text': text,
        'source': source,
        'allow_protected_paths': allowProtectedPaths,
        if (projectId != null) 'project_id': projectId,
      })));

  @override
  Future<DevRequest> cancel(String id) async => DevRequest.fromJson(JsonReader(
      await api.request('POST', '$_base/requests/${_id(id)}/cancel')));

  @override
  Future<List<DevProject>> projects() async =>
      JsonReader(await api.request('GET', '$_base/projects'))
          .objects('data', DevProject.fromJson);

  @override
  Future<DevProject> createProject(JsonMap body) async => DevProject.fromJson(
      JsonReader(await api.request('POST', '$_base/projects', body: body)));

  @override
  Future<DevProject> updateProject(String id, JsonMap body) async =>
      DevProject.fromJson(JsonReader(await api
          .request('PATCH', '$_base/projects/${_id(id)}', body: body)));

  @override
  Future<void> deleteProject(String id) async {
    await api.request('DELETE', '$_base/projects/${_id(id)}');
  }

  @override
  Future<DevProject> testProject(String id) async => DevProject.fromJson(
      JsonReader(await api.request('POST', '$_base/projects/${_id(id)}/test')));

  @override
  Future<WorkflowInstall> installWorkflow(String id) async {
    final value = JsonReader(await api.request(
        'POST', '$_base/projects/${_id(id)}/install-workflow'));
    return WorkflowInstall(
      project: DevProject.fromJson(value.object('project')),
      number: value.optInt('pull_request_number') ?? 0,
      url: value.optString('pull_request_url') ?? '',
      created: value.optBool('created') ?? false,
    );
  }

  @override
  Future<Resolution> resolve(String text) async =>
      Resolution.fromJson(JsonReader(
          await api.request('POST', '$_base/resolve', body: {'text': text})));

  @override
  Future<MergeReview> requestMerge(String id) async {
    final value = JsonReader(
        await api.request('POST', '$_base/requests/${_id(id)}/merge-approval'));
    return MergeReview(
      request: DevRequest.fromJson(value.object('request')),
      approval: workspaceApprovalFromJson(value.object('approval')),
    );
  }

  @override
  Future<String?> approve(WorkspaceApproval approval) async {
    // The same call the Approvals screen makes: the API refuses a stale hash
    // and the runtime runs only the call that still matches it.
    final value = JsonReader(await api.request(
        'POST', '/api/v1/approvals/${_id(approval.id)}/approve',
        body: decisionBody(approval, null)));
    return value.optObject('run')?.optString('result');
  }

  @override
  Future<String?> currentRole() async {
    final value = JsonReader(
        await api.request('GET', '/api/v1/workspace-memberships/current'));
    return value['active'] == false ? null : value.optString('role');
  }

  @override
  Future<String?> workspaceId() async =>
      (await api.sessions.read())?.context.workspaceId;
}
