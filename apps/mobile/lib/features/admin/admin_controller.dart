import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'admin_models.dart';
import 'admin_repository.dart';

/// `forbidden` comes from the API's 403: the client never guesses roles.
enum AdminPhase { initial, loading, ready, forbidden, offline, error }

String describeAdminError(Object error) {
  if (error is ApiException) {
    if (error.statusCode == 401) return 'Your sign-in expired. Sign in again.';
    if (error.isBudgetExceeded) {
      return 'Monthly model budget reached. ${error.message}';
    }
    return error.message;
  }
  final text = error.toString();
  return text.isEmpty ? 'Something went wrong.' : text;
}

bool _offline(Object error) =>
    (error is ApiException && error.statusCode == 0) ||
    error.toString().toLowerCase().contains('socket');

class MembersController extends ChangeNotifier {
  MembersController(this.repository);
  final AdminRepository repository;

  AdminPhase phase = AdminPhase.initial;
  List<WorkspaceMember> members = const [];
  List<WorkspaceInvitation> invitations = const [];

  /// The API's answer when listing is refused or fails.
  String? loadMessage;

  /// Result of the last action; [actionFailed] marks it as an error.
  String? notice;
  bool actionFailed = false;
  bool busy = false;

  /// Shown once after creation, until [dismissCreated].
  CreatedInvitation? created;

  List<WorkspaceInvitation> get pending =>
      invitations.where((x) => x.pending).toList(growable: false);

  Future<void> load() async {
    phase = AdminPhase.loading;
    loadMessage = null;
    notifyListeners();
    try {
      final values =
          await Future.wait([repository.members(), repository.invitations()]);
      members = sortMembers(values[0] as List<WorkspaceMember>);
      invitations = [
        ...(values[1] as List<WorkspaceInvitation>).where((x) => x.pending),
        ...(values[1] as List<WorkspaceInvitation>).where((x) => !x.pending),
      ];
      phase = AdminPhase.ready;
    } on Object catch (error) {
      loadMessage = describeAdminError(error);
      phase = error is ApiException && error.isPermissionDenied
          ? AdminPhase.forbidden
          : _offline(error)
              ? AdminPhase.offline
              : AdminPhase.error;
    }
    notifyListeners();
  }

  Future<bool> changeRole(WorkspaceMember member, String role) =>
      _act(() async {
        await repository.changeRole(member.userId, role);
        return '${member.userId} is now ${roleLabel(role).toLowerCase()}.';
      });

  Future<bool> setActive(WorkspaceMember member, {required bool active}) =>
      _act(() async {
        await repository.setActive(member.userId, active: active);
        return '${member.userId} was ${active ? 'reactivated' : 'deactivated'}.';
      });

  Future<bool> invite(InvitationDraft draft) => _act(() async {
        created = await repository.createInvitation(draft);
        return 'Invitation created. Copy the token now: it is shown only once.';
      });

  Future<bool> revoke(WorkspaceInvitation invitation) => _act(() async {
        await repository.revokeInvitation(invitation.id);
        return 'Invitation for ${invitation.invitee} was revoked.';
      });

  void dismissCreated() {
    created = null;
    notifyListeners();
  }

  Future<bool> _act(Future<String> Function() action) async {
    busy = true;
    notice = null;
    actionFailed = false;
    notifyListeners();
    try {
      final message = await action();
      await load();
      notice = message;
      return true;
    } on Object catch (error) {
      actionFailed = true;
      notice = error is ApiException && error.isPermissionDenied
          ? 'Not allowed: ${error.message}'
          : describeAdminError(error);
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }
}

/// Redeeming a token works for anyone signed in; no membership is needed.
class AcceptInvitationController extends ChangeNotifier {
  AcceptInvitationController(this.repository,
      {required this.currentWorkspaceId});
  final AdminRepository repository;
  final String currentWorkspaceId;
  bool busy = false;
  String? message;
  bool failed = false;
  AcceptedInvitation? accepted;

  Future<bool> accept(String input, {String workspaceId = ''}) async {
    final parsed = parseInvitationInput(input);
    if (parsed == null) {
      failed = true;
      message =
          'Paste an invitation token (it starts with anum_inv_) or the whole invitation link.';
      notifyListeners();
      return false;
    }
    final target = parsed.workspaceId ??
        (workspaceId.trim().isEmpty ? null : workspaceId.trim());
    busy = true;
    message = null;
    failed = false;
    notifyListeners();
    try {
      accepted =
          await repository.acceptInvitation(parsed.token, workspaceId: target);
      final joined = accepted!.member.workspaceId;
      message =
          'You joined $joined as ${roleLabel(accepted!.member.role).toLowerCase()}.'
          '${joined == currentWorkspaceId ? '' : ' Switch to that workspace to work in it.'}';
      return true;
    } on Object catch (error) {
      failed = true;
      message = describeAdminError(error);
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }
}

class BudgetsController extends ChangeNotifier {
  BudgetsController(this.repository);
  final AdminRepository repository;
  AdminPhase phase = AdminPhase.initial;
  BudgetOverview? overview;
  String? loadMessage;

  /// Per scope ('tenant' / 'workspace'): the last save result.
  final Map<String, String> notices = {};
  final Set<String> failedScopes = {};
  String? savingScope;

  Future<void> load() async {
    phase = AdminPhase.loading;
    loadMessage = null;
    notifyListeners();
    try {
      overview = await repository.budgets();
      phase = AdminPhase.ready;
    } on Object catch (error) {
      loadMessage = describeAdminError(error);
      phase = error is ApiException && error.isPermissionDenied
          ? AdminPhase.forbidden
          : _offline(error)
              ? AdminPhase.offline
              : AdminPhase.error;
    }
    notifyListeners();
  }

  Future<bool> save(String scope, String cost, String tokens) async {
    final (limits, error) = parseLimits(cost, tokens);
    notices.remove(scope);
    failedScopes.remove(scope);
    if (limits == null) {
      notices[scope] = error!;
      failedScopes.add(scope);
      notifyListeners();
      return false;
    }
    savingScope = scope;
    notifyListeners();
    try {
      overview = await repository.setBudget(scope, limits);
      notices[scope] = 'Budget saved.';
      return true;
    } on Object catch (e) {
      failedScopes.add(scope);
      notices[scope] = e is ApiException && e.isPermissionDenied
          ? 'Only owners can change budgets. ${e.message}'
          : describeAdminError(e);
      return false;
    } finally {
      savingScope = null;
      notifyListeners();
    }
  }
}
