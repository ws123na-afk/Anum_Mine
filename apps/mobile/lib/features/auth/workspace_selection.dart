import '../../data/api_models.dart';

/// Remembers which workspace a person picked, per tenant and user, so the
/// next sign-in on this device opens it without asking again
/// (docs/mobile.md#first-run). The app keeps it in platform secure storage
/// (`SecureWorkspacePreferenceStore`); it holds a workspace id, never a
/// credential, and is used only while that workspace is still listed among
/// the caller's active memberships.
abstract interface class WorkspacePreferenceStore {
  Future<String?> read({required String tenantId, required String userId});
  Future<void> write(
      {required String tenantId,
      required String userId,
      required String workspaceId});
}

class MemoryWorkspacePreferenceStore implements WorkspacePreferenceStore {
  final Map<(String, String), String> _items = {};

  @override
  Future<String?> read(
          {required String tenantId, required String userId}) async =>
      _items[(tenantId, userId)];

  @override
  Future<void> write(
      {required String tenantId,
      required String userId,
      required String workspaceId}) async {
    _items[(tenantId, userId)] = workspaceId;
  }
}

/// Where a signed-in session stands with its workspace.
sealed class WorkspaceResolution {
  const WorkspaceResolution();
}

/// The session names a workspace: the token's `workspace_id` claim, the
/// build's `ANUM_WORKSPACE_ID`, the only membership, or the remembered choice.
final class WorkspaceResolved extends WorkspaceResolution {
  const WorkspaceResolved(this.workspaceId);
  final String workspaceId;
}

/// Several memberships and no usable remembered choice: the person picks.
final class WorkspaceChoiceNeeded extends WorkspaceResolution {
  const WorkspaceChoiceNeeded(this.memberships);
  final List<CallerMembership> memberships;
}

/// The account has no active membership in any workspace of its tenant.
final class NoWorkspaceMembership extends WorkspaceResolution {
  const NoWorkspaceMembership();
}
