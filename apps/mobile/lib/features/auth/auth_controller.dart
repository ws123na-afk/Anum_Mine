import 'package:flutter/foundation.dart';

import '../../data/api_models.dart';
import 'auth_repository.dart';
import 'oidc.dart';
import 'sign_in_options.dart';
import 'workspace_selection.dart';

enum AuthPhase {
  restoring,
  signedOut,

  /// Signed in with several workspaces and no remembered choice.
  workspacePicker,

  /// Signed in, but a member of no workspace.
  noWorkspace,

  /// Signed in, but the account belongs to no organization yet
  /// (docs/identity.md#pending-accounts).
  accountPending,
  onboarding,
  modelSetup,
  ready,
  busy,
  error
}

enum AuthIssue {
  none,
  offline,
  permission,
  invalidSession,
  validation,
  unknown
}

class AuthController extends ChangeNotifier {
  AuthController(this.repository);

  final AuthRepository repository;
  AuthPhase phase = AuthPhase.restoring;
  String? message;
  AuthIssue issue = AuthIssue.none;
  AuthPhase _retryPhase = AuthPhase.signedOut;

  /// The organization and workspace the user is signed in to, as reported by the API.
  OnboardingStatus? onboarding;
  String? get workspaceName => onboarding?.workspace?.name;
  String? get organizationName => onboarding?.tenant?.name;

  /// The memberships offered while [phase] is [AuthPhase.workspacePicker].
  List<CallerMembership> workspaceChoices = const [];

  /// Whether skipping the model step is possible, once checked
  /// ([checkModelAvailability]); null while unknown.
  ModelAvailability? modelAvailability;
  bool checkingModel = false;

  /// Why the model check failed, or null.
  String? modelCheckError;

  /// The caller may save the workspace's model (`organization:manage`, owners).
  bool get canConfigureModel => onboarding?.membership?.role == 'owner';

  Future<void> restore() async {
    try {
      final session = await repository.restoreSession();
      if (session == null) {
        phase = AuthPhase.signedOut;
      } else {
        await _routeSignedIn();
      }
    } on Object catch (error) {
      phase = AuthPhase.error;
      _setError(error);
    }
    notifyListeners();
  }

  Future<void> signIn(String tenant, String workspace, String user,
      {String? password}) async {
    if ([tenant, workspace, user].any((value) => value.trim().length < 3)) {
      phase = AuthPhase.error;
      issue = AuthIssue.validation;
      message = 'Enter valid organization, workspace, and user identifiers.';
      _retryPhase = AuthPhase.signedOut;
      notifyListeners();
      return;
    }
    await _run(AuthPhase.signedOut, () async {
      await repository.startLocalSession(
        tenantId: tenant.trim(),
        workspaceId: workspace.trim(),
        userId: user.trim(),
        password: password?.trim(),
      );
      await _routeSignedIn();
    });
  }

  /// True when the build names an OIDC issuer (`--dart-define=ANUM_OIDC_ISSUER`).
  bool get oidcEnabled => repository.usesOidc;

  /// Keycloak's "Forgot password?" page, or null without OIDC.
  Uri? get passwordResetUri => repository.oidc?.config.passwordResetUri;

  /// What the sign-in screens offer (`GET /api/v1/auth/sign-in-options`);
  /// null while loading.
  SignInOptions? signInOptions;

  /// Why the options could not be loaded (the plain sign-in still works).
  String? signInOptionsError;

  Future<void> loadSignInOptions() async {
    signInOptionsError = null;
    try {
      signInOptions = await repository.signInOptions();
    } on Object catch (error) {
      signInOptions = SignInOptions.none;
      signInOptionsError = _describe(error);
    }
    notifyListeners();
  }

  /// The pending account while [phase] is [AuthPhase.accountPending].
  PendingAccount? pendingAccount;
  OidcTokens? _pendingTokens;
  bool checkingPending = false;

  /// The last "Check again" found no organization yet.
  bool stillPending = false;

  /// Why the last "Check again" failed, or null.
  String? pendingError;

  /// Signs in through Keycloak in the system browser (authorization code +
  /// PKCE). [options] can prefill the username, go straight to Google or
  /// Apple, or open registration.
  Future<void> signInWithOidc(
      [OidcSignInOptions options = const OidcSignInOptions()]) async {
    await _run(AuthPhase.signedOut, () async {
      try {
        await repository.signInWithOidc(options);
      } on OidcCancelled {
        phase = AuthPhase.signedOut;
        return;
      } on AccountPending catch (pending) {
        _pend(pending);
        return;
      }
      await _routeSignedIn();
    });
  }

  void _pend(AccountPending pending) {
    _pendingTokens = pending.tokens;
    pendingAccount = pending.account;
    phase = AuthPhase.accountPending;
  }

  /// Fetches a fresh token: it names the organization once an administrator
  /// assigned one.
  Future<void> checkPendingAgain() async {
    final tokens = _pendingTokens;
    if (tokens == null) return;
    checkingPending = true;
    stillPending = false;
    pendingError = null;
    notifyListeners();
    try {
      await repository.recheckPending(tokens);
      _pendingTokens = null;
      pendingAccount = null;
      checkingPending = false;
      await _run(AuthPhase.signedOut, _routeSignedIn);
      return;
    } on AccountPending catch (pending) {
      _pend(pending);
      stillPending = true;
    } on OidcRefreshRejected {
      _pendingTokens = null;
      pendingAccount = null;
      phase = AuthPhase.signedOut;
      message = 'Your session ended. Sign in again.';
    } on Object catch (error) {
      pendingError = _describe(error);
    }
    checkingPending = false;
    notifyListeners();
  }

  /// Leaves the pending account (ends the Keycloak session in the browser).
  Future<void> signOutPending() async {
    final tokens = _pendingTokens;
    _pendingTokens = null;
    pendingAccount = null;
    stillPending = false;
    pendingError = null;
    phase = AuthPhase.signedOut;
    notifyListeners();
    if (tokens != null) await repository.endPendingSession(tokens);
  }

  Future<void> completeExternalSignIn(Future<Object?> Function() action) async {
    await _run(AuthPhase.signedOut, () async {
      await action();
      await _routeSignedIn();
    });
  }

  Future<void> createWorkspace(String organization, String workspace) async {
    if (organization.trim().isEmpty || workspace.trim().isEmpty) {
      phase = AuthPhase.error;
      issue = AuthIssue.validation;
      message = 'Organization and workspace names are required.';
      _retryPhase = AuthPhase.onboarding;
      notifyListeners();
      return;
    }
    await _run(AuthPhase.onboarding, () async {
      final status = onboarding = await repository.completeOnboarding(
        organizationName: organization.trim(),
        workspaceName: workspace.trim(),
      );
      _routeOnboarded(status);
    });
  }

  /// Selects one of [workspaceChoices]; the API confirms the membership first
  /// and the choice is remembered for the next sign-in on this device.
  Future<void> chooseWorkspace(String workspaceId) async {
    await _run(AuthPhase.workspacePicker, () async {
      await repository.switchWorkspace(workspaceId);
      await _routeSignedIn();
    });
  }

  /// Looks again for a workspace (after an owner added the person).
  Future<void> checkWorkspacesAgain() async {
    await _run(AuthPhase.noWorkspace, _routeSignedIn);
  }

  /// Checks whether skipping the model step would leave a model answering.
  Future<void> checkModelAvailability() async {
    checkingModel = true;
    modelCheckError = null;
    notifyListeners();
    try {
      final availability =
          modelAvailability = await repository.modelAvailability();
      if (availability.workspaceConfigured) {
        // Someone (an owner, from another device) saved one meanwhile.
        onboarding = await repository.onboardingStatus();
      }
    } on Object catch (error) {
      modelAvailability = null;
      modelCheckError = _describe(error);
    } finally {
      checkingModel = false;
    }
    notifyListeners();
  }

  Future<void> _routeSignedIn() async {
    switch (await repository.resolveWorkspace()) {
      case WorkspaceChoiceNeeded(:final memberships):
        workspaceChoices = memberships;
        phase = AuthPhase.workspacePicker;
        return;
      case NoWorkspaceMembership():
        workspaceChoices = const [];
        phase = AuthPhase.noWorkspace;
        return;
      case WorkspaceResolved():
        workspaceChoices = const [];
    }
    _routeOnboarded(onboarding = await repository.onboardingStatus());
  }

  void _routeOnboarded(OnboardingStatus status) {
    if (!status.complete) {
      phase = AuthPhase.onboarding;
    } else if (status.modelConfigured) {
      phase = AuthPhase.ready;
    } else {
      phase = AuthPhase.modelSetup;
      modelAvailability = null;
      modelCheckError = null;
      checkingModel = true;
      // Runs after this phase change is announced; it notifies again when done.
      Future<void>.microtask(checkModelAvailability);
    }
  }

  Future<void> connectModel({
    required String provider,
    required String model,
    required String baseUrl,
    required String apiKey,
    ModelRouting? routing,
  }) async {
    await _run(AuthPhase.modelSetup, () async {
      await repository.configureModel(
        provider: provider,
        model: model,
        baseUrl: baseUrl,
        apiKey: apiKey,
        routing: routing,
      );
      await repository.testModelConnection();
      phase = AuthPhase.ready;
    });
  }

  /// Continues without saving a workspace model. Offered only when
  /// [modelAvailability] says a model answers: the workspace's saved one or
  /// the server's configured one (docs/mobile.md#first-run).
  void useServerModel() {
    if (modelAvailability?.canSkip != true) return;
    message = null;
    issue = AuthIssue.none;
    phase = AuthPhase.ready;
    notifyListeners();
  }

  Future<void> signOut() async {
    await repository.signOut();
    onboarding = null;
    workspaceChoices = const [];
    modelAvailability = null;
    modelCheckError = null;
    phase = AuthPhase.signedOut;
    notifyListeners();
  }

  void retry() {
    message = null;
    issue = AuthIssue.none;
    phase = _retryPhase;
    notifyListeners();
  }

  Future<void> _run(
      AuthPhase retryPhase, Future<void> Function() action) async {
    _retryPhase = retryPhase;
    phase = AuthPhase.busy;
    message = null;
    issue = AuthIssue.none;
    notifyListeners();
    try {
      await action();
    } on Object catch (error) {
      phase = AuthPhase.error;
      _setError(error);
    }
    notifyListeners();
  }

  static String _describe(Object error) => error
      .toString()
      .replaceFirst(RegExp(r'^ApiException\(\d+,\s*'), '')
      .replaceFirst(RegExp(r'\)$'), '');

  void _setError(Object error) {
    final text = error.toString();
    message = _describe(error);
    issue = text.contains('(401,')
        ? AuthIssue.invalidSession
        : text.contains('(403,')
            ? AuthIssue.permission
            : text.toLowerCase().contains('socket') ||
                    text.toLowerCase().contains('network')
                ? AuthIssue.offline
                : AuthIssue.unknown;
  }
}
