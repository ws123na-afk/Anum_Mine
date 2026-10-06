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

  Future<List<T>> _list<T>(
          String path, T Function(JsonMap json, String path) read) async =>
      JsonReader(await api.request('GET', path))
          .optObjects('data', (x) => read(x.json, x.path));

  @override
  Future<List<WorkspaceMember>> members() =>
      _list('/api/v1/workspace-members', WorkspaceMember.fromJson);

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
  Future<List<WorkspaceInvitation>> invitations() =>
      _list('/api/v1/workspace-invitations', WorkspaceInvitation.fromJson);

  @override
  Future<CreatedInvitation> createInvitation(InvitationDraft draft) async {
    final value = JsonReader(await api.request(
        'POST', '/api/v1/workspace-invitations',
        body: draft.toJson()));
    final invitation = value.object('invitation');
    return CreatedInvitation(
      invitation:
          WorkspaceInvitation.fromJson(invitation.json, invitation.path),
      token: value.string('token'),
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
    final value = JsonReader(await api.request(
      'POST',
      '/api/v1/workspace-invitations/accept',
      body: {'token': token},
      headers: {
        if (workspaceId != null && workspaceId.isNotEmpty)
          'x-workspace-id': workspaceId
      },
    ));
    final invitation = value.object('invitation');
    final membership = value.object('membership');
    return AcceptedInvitation(
      invitation:
          WorkspaceInvitation.fromJson(invitation.json, invitation.path),
      member: WorkspaceMember.fromJson(membership.json, membership.path),
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
    final value = JsonReader(
        await api.request('GET', '/api/v1/workspace-memberships/current'));
    return value['active'] == false ? null : value.optString('role');
  }
}
