import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'admin_models.dart';
import 'approval_policy.dart';

/// Owner screens' data: memberships, invitations, model budgets and the
/// workspace approval policy.
abstract interface class AdminRepository {
  Future<List<WorkspaceMember>> members();
  Future<WorkspaceMember> changeRole(String userId, String role);
  Future<WorkspaceMember> setActive(String userId, {required bool active});
  Future<List<WorkspaceInvitation>> invitations();
  Future<CreatedInvitation> createInvitation(InvitationDraft draft);
  Future<WorkspaceInvitation> revokeInvitation(String id);
  Future<AcceptedInvitation> acceptInvitation(String token,
      {String? workspaceId});
  Future<BudgetOverview> budgets();
  Future<BudgetOverview> setBudget(String scope, ModelBudgetLimits limits);
  Future<ApprovalPolicy> approvalPolicy();
  Future<ApprovalPolicy> setApprovalPolicy(ApprovalPolicy policy);

  /// The caller's role from their persisted membership in this workspace,
  /// or null when it is inactive or unknown.
  Future<String?> currentRole();
}

class ApiAdminRepository implements AdminRepository {
  const ApiAdminRepository(this.api);
  final AnumApiClient api;

  static String _id(String value) => Uri.encodeComponent(value);

  Future<List<JsonMap>> _list(String path) async {
    final value = await api.request('GET', path);
    return ((value['data'] as List<Object?>?) ?? const []).cast<JsonMap>();
  }

  @override
  Future<List<WorkspaceMember>> members() async =>
      (await _list('/api/v1/workspace-members'))
          .map(WorkspaceMember.fromJson)
          .toList();

  @override
  Future<WorkspaceMember> changeRole(String userId, String role) async =>
      WorkspaceMember.fromJson(await api.request(
          'PUT', '/api/v1/workspace-members/${_id(userId)}/role',
          body: {'role': role}));

  @override
  Future<WorkspaceMember> setActive(String userId,
          {required bool active}) async =>
      WorkspaceMember.fromJson(await api.request('POST',
          '/api/v1/workspace-members/${_id(userId)}/${active ? 'reactivate' : 'deactivate'}'));

  @override
  Future<List<WorkspaceInvitation>> invitations() async =>
      (await _list('/api/v1/workspace-invitations'))
          .map(WorkspaceInvitation.fromJson)
          .toList();

  @override
  Future<CreatedInvitation> createInvitation(InvitationDraft draft) async {
    final value = await api.request('POST', '/api/v1/workspace-invitations',
        body: draft.toJson());
    return CreatedInvitation(
      invitation: WorkspaceInvitation.fromJson(value['invitation']! as JsonMap),
      token: value['token']! as String,
    );
  }

  @override
  Future<WorkspaceInvitation> revokeInvitation(String id) async =>
      WorkspaceInvitation.fromJson(await api.request(
          'POST', '/api/v1/workspace-invitations/${_id(id)}/revoke'));

  /// The API accepts into the workspace named by `x-workspace-id`, so a
  /// link's workspace overrides the session's.
  @override
  Future<AcceptedInvitation> acceptInvitation(String token,
      {String? workspaceId}) async {
    final value = await api.request(
      'POST',
      '/api/v1/workspace-invitations/accept',
      body: {'token': token},
      headers: {
        if (workspaceId != null && workspaceId.isNotEmpty)
          'x-workspace-id': workspaceId
      },
    );
    return AcceptedInvitation(
      invitation: WorkspaceInvitation.fromJson(value['invitation']! as JsonMap),
      member: WorkspaceMember.fromJson(value['membership']! as JsonMap),
    );
  }

  @override
  Future<BudgetOverview> budgets() async => BudgetOverview.fromJson(
      await api.request('GET', '/api/v1/model-budgets'));

  @override
  Future<BudgetOverview> setBudget(
          String scope, ModelBudgetLimits limits) async =>
      BudgetOverview.fromJson(await api.request(
          'PUT', '/api/v1/model-budgets/$scope',
          body: limits.toJson()));

  @override
  Future<ApprovalPolicy> approvalPolicy() async => ApprovalPolicy.fromJson(
      await api.request('GET', '/api/v1/approval-policy'));

  /// Owners only: anyone else gets 403. Every change is audited.
  @override
  Future<ApprovalPolicy> setApprovalPolicy(ApprovalPolicy policy) async =>
      ApprovalPolicy.fromJson(await api
          .request('PUT', '/api/v1/approval-policy', body: policy.toJson()));

  @override
  Future<String?> currentRole() async {
    final value =
        await api.request('GET', '/api/v1/workspace-memberships/current');
    return value['active'] == false ? null : value['role'] as String?;
  }
}
