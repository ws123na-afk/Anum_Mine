import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';
import 'oidc.dart';
import 'workspace_selection.dart';

/// Told when the signed-in workspace is about to change and when it has
/// (phone push unregisters and registers here, docs/notifications.md).
abstract interface class WorkspaceSessionListener {
  /// Sign-out or a workspace switch is about to happen; the session still works.
  Future<void> leavingWorkspace();

  /// A workspace switch finished (or failed and the old workspace stays).
  Future<void> enteredWorkspace();
}

class AuthRepository {
  const AuthRepository(
      {required this.api,
      required this.sessions,
      this.oidc,
      this.workspacePreferences,
      this.sessionListener});

  /// Phone push, in the app; null in most tests.
  final WorkspaceSessionListener? sessionListener;

  Future<void> _leaving() async {
    try {
      await sessionListener
          ?.leavingWorkspace()
          .timeout(const Duration(seconds: 5));
    } on Object {
      // Never block signing out or switching on push.
    }
  }

  Future<void> _entered() async {
    try {
      await sessionListener?.enteredWorkspace();
    } on Object {
      // Push reports its own errors in Settings.
    }
  }

  final AnumApiClient api;
  final SessionStore sessions;

  /// Remembers the workspace picked on this device (secure storage in the
  /// app). Without it a person with several workspaces picks at each sign-in.
  final WorkspacePreferenceStore? workspacePreferences;

  /// Set when the build names an OIDC issuer; sign-in then goes through
  /// Keycloak and the local endpoints below are not used.
  final OidcAuthenticator? oidc;

  bool get usesOidc => oidc != null;

  /// Authorization code + PKCE in the system browser, then the tokens are kept
  /// in platform secure storage.
  /// [options] carries Keycloak hints only (login hint, Google or Apple,
  /// registration); the password is always entered on Keycloak's page.
  Future<LocalSession> signInWithOidc(
      [OidcSignInOptions options = const OidcSignInOptions()]) async {
    final authenticator = oidc;
    if (authenticator == null) {
      throw StateError('OIDC sign-in is not configured');
    }
    final session = sessionFromOidcTokens(await authenticator.signIn(options),
        defaultWorkspaceId: authenticator.config.defaultWorkspaceId);
    await sessions.write(session);
    return session;
  }

  Future<LocalSession> startLocalSession({
    required String tenantId,
    required String workspaceId,
    required String userId,
    String? password,
  }) async {
    final json = await api.request(
      'POST',
      '/api/v1/auth/local/session',
      authenticated: false,
      body: {
        'tenant_id': tenantId,
        'workspace_id': workspaceId,
        'user_id': userId,
        if (password != null && password.isNotEmpty) 'password': password,
      },
    );
    final session = LocalSession.fromJson(json);
    await sessions.write(session);
    return session;
  }

  Future<String> requestOtp(
      {required String tenantId,
      required String workspaceId,
      required String userId}) async {
    final value = await api.request('POST', '/api/v1/auth/local/otp/request',
        authenticated: false,
        body: {
          'tenant_id': tenantId,
          'workspace_id': workspaceId,
          'user_id': userId
        });
    return JsonReader(value).string('challenge_id');
  }

  Future<LocalSession> verifyOtp(
          {required String challengeId, required String code}) =>
      _acceptSession(api.request('POST', '/api/v1/auth/local/otp/verify',
          authenticated: false,
          body: {'challenge_id': challengeId, 'code': code}));

  Future<String> requestPasswordReset(
      {required String tenantId,
      required String workspaceId,
      required String userId}) async {
    final value = await api.request(
        'POST', '/api/v1/auth/local/password/forgot',
        authenticated: false,
        body: {
          'tenant_id': tenantId,
          'workspace_id': workspaceId,
          'user_id': userId
        });
    return JsonReader(value).string('challenge_id');
  }

  Future<LocalSession> resetPassword(
          {required String challengeId,
          required String token,
          required String newPassword}) =>
      _acceptSession(api.request('POST', '/api/v1/auth/local/password/reset',
          authenticated: false,
          body: {
            'challenge_id': challengeId,
            'token': token,
            'new_password': newPassword
          }));

  /// The caller's active memberships in every workspace of their tenant,
  /// sorted by the API (name, then id). The picker still checks the membership
  /// in the chosen workspace before switching.
  Future<List<CallerMembership>> myWorkspaces() async {
    final value = await api.request('GET', '/api/v1/me/workspace-memberships');
    return JsonReader(value).optObjects(
        'data', (item) => CallerMembership.fromJson(item.json, item.path));
  }

  Future<LocalSession> switchWorkspace(String workspaceId) async {
    final current = await sessions.read();
    if (current != null &&
        current.context.workspaceId.isNotEmpty &&
        current.context.workspaceId != workspaceId) {
      return _switchAnnounced(current, workspaceId);
    }
    return _switch(current, workspaceId);
  }

  Future<LocalSession> _switchAnnounced(
      LocalSession current, String workspaceId) async {
    await _leaving();
    try {
      return await _switch(current, workspaceId);
    } finally {
      await _entered();
    }
  }

  Future<LocalSession> _switch(
      LocalSession? current, String workspaceId) async {
    if (current != null && current.isOidc) {
      // Ask the API first: only an active membership in the target workspace
      // switches. A refusal (403) throws and leaves the session unchanged.
      final membership = MembershipSummary.fromJson(await api.request(
          'GET', '/api/v1/workspace-memberships/current',
          headers: {'x-workspace-id': workspaceId}));
      if (!membership.active || membership.workspaceId != workspaceId) {
        throw ApiException(
            403, 'You do not have an active membership in $workspaceId.');
      }
      // The token stays the same; the API resolves the membership for the
      // workspace named in x-workspace-id on each request.
      final next = current.copyWith(
          context: TenantContext(
              tenantId: current.context.tenantId,
              workspaceId: workspaceId,
              userId: current.context.userId,
              roles: current.context.roles));
      await sessions.write(next);
      await workspacePreferences?.write(
          tenantId: next.context.tenantId,
          userId: next.context.userId,
          workspaceId: workspaceId);
      return next;
    }
    return _acceptSession(api.request(
        'POST', '/api/v1/auth/local/workspace/switch',
        body: {'workspace_id': workspaceId}));
  }

  /// Gives a signed-in session a workspace when it has none.
  ///
  /// A Keycloak user without the `default_workspace_id` attribute gets a token
  /// without a `workspace_id` claim; unless the build sets `ANUM_WORKSPACE_ID`
  /// the session then names no workspace and every workspace call would answer
  /// 400. The caller's memberships decide instead: exactly one is selected,
  /// the remembered choice is selected while it is still listed, several ask
  /// the person to pick, none is reported (docs/mobile.md#first-run).
  Future<WorkspaceResolution> resolveWorkspace() async {
    final session = await sessions.read();
    if (session == null) {
      throw const ApiException(401, 'Authentication required');
    }
    if (session.context.workspaceId.isNotEmpty) {
      return WorkspaceResolved(session.context.workspaceId);
    }
    final memberships = await myWorkspaces();
    if (memberships.isEmpty) return const NoWorkspaceMembership();
    final remembered = await workspacePreferences?.read(
        tenantId: session.context.tenantId, userId: session.context.userId);
    final pick = memberships.length == 1
        ? memberships.single
        : memberships.where((m) => m.workspaceId == remembered).firstOrNull;
    if (pick == null) return WorkspaceChoiceNeeded(memberships);
    await switchWorkspace(pick.workspaceId);
    return WorkspaceResolved(pick.workspaceId);
  }

  /// What answers when this workspace saves no model of its own: the
  /// workspace's saved model (`model_configured` of `GET /api/v1/onboarding`)
  /// or the server's configured model (the `configured-model` routing target
  /// of `GET /api/v1/routing/targets`). The built-in mock planner does not
  /// count: it answers with canned plans, not a model.
  Future<ModelAvailability> modelAvailability() async {
    final status = await onboardingStatus();
    if (status.modelConfigured) {
      return const ModelAvailability(workspaceConfigured: true);
    }
    final targets =
        JsonReader(await api.request('GET', '/api/v1/routing/targets'))
            .optObjects('data', (item) => item);
    final server = targets
        .where((target) => target.optString('id') == 'configured-model')
        .firstOrNull;
    final provider = server?.optString('provider');
    final model = server?.optString('model');
    if (server == null ||
        provider == null ||
        provider == 'mock' ||
        model == null ||
        model.isEmpty) {
      return const ModelAvailability(workspaceConfigured: false);
    }
    return ModelAvailability(workspaceConfigured: false, serverModel: model);
  }

  Future<LocalSession> _acceptSession(Future<JsonMap> response) async {
    final session = LocalSession.fromJson(await response);
    await sessions.write(session);
    return session;
  }

  Future<LocalSession?> restoreSession() async {
    final session = await sessions.read();
    if (session == null) return null;
    if (session.isExpired) {
      await sessions.clear();
      return null;
    }
    return session;
  }

  Future<void> signOut() async {
    await _leaving();
    LocalSession? session;
    try {
      session = await sessions.read();
    } on Object {
      session = null;
    }
    if (session != null && session.isOidc) {
      try {
        await oidc?.endSession(idToken: session.idToken);
      } on Object {
        // Cancelling or failing the provider logout still signs this device
        // out; the provider session then ends on its own idle timeout.
      } finally {
        await sessions.clear();
      }
      return;
    }
    try {
      await api.request('DELETE', '/api/v1/auth/local/session');
    } finally {
      await sessions.clear();
    }
  }

  Future<OnboardingStatus> onboardingStatus() async =>
      OnboardingStatus.fromJson(await api.request('GET', '/api/v1/onboarding'));

  Future<OnboardingStatus> completeOnboarding({
    required String organizationName,
    required String workspaceName,
  }) async =>
      OnboardingStatus.fromJson(await api.request(
        'PUT',
        '/api/v1/onboarding',
        body: {
          'organization_name': organizationName,
          'workspace_name': workspaceName,
        },
      ));

  /// Saves the workspace model. [routing] is sent for `anthropic` only; its
  /// base URL may be empty (the API then uses https://api.anthropic.com).
  Future<ModelConfiguration> configureModel({
    required String provider,
    required String model,
    required String baseUrl,
    String? apiKey,
    ModelRouting? routing,
  }) async {
    final claude = provider == 'anthropic';
    return ModelConfiguration.fromJson(await api.request(
      'PUT',
      '/api/v1/model-config',
      body: {
        'provider': provider,
        'model': model,
        if (!claude || baseUrl.trim().isNotEmpty) 'base_url': baseUrl,
        if (apiKey != null && apiKey.isNotEmpty) 'api_key': apiKey,
        if (claude) ...(routing ?? const ModelRouting()).toJson(),
      },
    ));
  }

  Future<ModelConfiguration> modelConfiguration() async =>
      ModelConfiguration.fromJson(
          await api.request('GET', '/api/v1/model-config'));

  Future<void> testModelConnection() async {
    await api.request('POST', '/api/v1/model-config/test');
  }

  Future<NotificationPreferences> notificationPreferences() async =>
      NotificationPreferences.fromJson(
        await api.request('GET', '/api/v1/notification-preferences'),
      );

  /// The caller's own "Remember things from my chats" switch.
  Future<ChatMemoryPreference> chatMemoryPreference() async =>
      ChatMemoryPreference.fromJson(
          await api.request('GET', '/api/v1/me/preferences'));

  Future<ChatMemoryPreference> setRememberConversations(bool remember) async =>
      ChatMemoryPreference.fromJson(await api.request(
          'PUT', '/api/v1/me/preferences',
          body: {'remember_conversations': remember}));

  /// Deletes every memory remembered from the caller's own chats in this
  /// workspace; returns how many.
  Future<int> deleteMyChatMemories() async {
    final result = await api.request(
        'DELETE', '/api/v1/memories?source_type=conversation');
    return (result['deleted'] as num?)?.toInt() ?? 0;
  }

  Future<NotificationPreferences> updateNotificationPreferences(
    NotificationPreferences value,
  ) async =>
      NotificationPreferences.fromJson(await api.request(
        'PUT',
        '/api/v1/notification-preferences',
        body: value.toJson(),
      ));
}
