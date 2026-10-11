import 'json_read.dart';

export 'json_read.dart';

typedef JsonMap = Map<String, Object?>;

class TenantContext {
  const TenantContext({
    required this.tenantId,
    required this.workspaceId,
    required this.userId,
    required this.roles,
  });

  factory TenantContext.fromJson(JsonMap json, [String path = '']) {
    final r = JsonReader(json, path);
    return TenantContext(
      tenantId: r.string('tenant_id'),
      workspaceId: r.string('workspace_id'),
      userId: r.string('user_id'),
      roles: r.strings('roles'),
    );
  }

  final String tenantId;
  final String workspaceId;
  final String userId;
  final List<String> roles;

  JsonMap toJson() => {
        'tenant_id': tenantId,
        'workspace_id': workspaceId,
        'user_id': userId,
        'roles': roles,
      };
}

/// How a session was established. `local` sessions come from the development
/// `/api/v1/auth/local/*` endpoints; `oidc` sessions hold Keycloak tokens.
enum SessionSource { local, oidc }

class LocalSession {
  const LocalSession({
    required this.accessToken,
    required this.tokenType,
    required this.expiresAt,
    required this.context,
    this.source = SessionSource.local,
    this.refreshToken,
    this.idToken,
  });

  factory LocalSession.fromJson(JsonMap json) {
    final r = JsonReader(json);
    final context = r.object('context');
    return LocalSession(
      accessToken: r.string('access_token'),
      tokenType: r.string('token_type'),
      expiresAt: r.date('expires_at').toUtc(),
      context: TenantContext.fromJson(context.json, context.path),
      source: json['source'] == SessionSource.oidc.name
          ? SessionSource.oidc
          : SessionSource.local,
      refreshToken: r.optString('refresh_token'),
      idToken: r.optString('id_token'),
    );
  }

  final String accessToken;
  final String tokenType;
  final DateTime expiresAt;
  final TenantContext context;
  final SessionSource source;

  /// OIDC only. Kept with the session in platform secure storage.
  final String? refreshToken;

  /// OIDC only. Sent as `id_token_hint` when ending the provider session.
  final String? idToken;

  bool get isOidc => source == SessionSource.oidc;

  bool get isExpired => !expiresAt.isAfter(DateTime.now().toUtc());

  LocalSession copyWith({
    String? accessToken,
    DateTime? expiresAt,
    TenantContext? context,
    String? refreshToken,
    String? idToken,
  }) =>
      LocalSession(
        accessToken: accessToken ?? this.accessToken,
        tokenType: tokenType,
        expiresAt: expiresAt ?? this.expiresAt,
        context: context ?? this.context,
        source: source,
        refreshToken: refreshToken ?? this.refreshToken,
        idToken: idToken ?? this.idToken,
      );

  JsonMap toJson() => {
        'access_token': accessToken,
        'token_type': tokenType,
        'expires_at': expiresAt.toIso8601String(),
        'context': context.toJson(),
        'source': source.name,
        if (refreshToken != null) 'refresh_token': refreshToken,
        if (idToken != null) 'id_token': idToken,
      };
}

class TenantSummary {
  const TenantSummary({required this.id, required this.name});

  factory TenantSummary.fromJson(JsonMap json, [String path = '']) {
    final r = JsonReader(json, path);
    return TenantSummary(id: r.string('id'), name: r.string('name'));
  }

  final String id;
  final String name;
}

class WorkspaceSummary {
  const WorkspaceSummary({
    required this.id,
    required this.tenantId,
    required this.name,
  });

  factory WorkspaceSummary.fromJson(JsonMap json, [String path = '']) {
    final r = JsonReader(json, path);
    return WorkspaceSummary(
      id: r.string('id'),
      tenantId: r.string('tenant_id'),
      name: r.string('name'),
    );
  }

  final String id;
  final String tenantId;
  final String name;
}

class MembershipSummary {
  const MembershipSummary({
    required this.tenantId,
    required this.workspaceId,
    required this.userId,
    required this.role,
    required this.active,
  });

  factory MembershipSummary.fromJson(JsonMap json, [String path = '']) {
    final r = JsonReader(json, path);
    return MembershipSummary(
      tenantId: r.string('tenant_id'),
      workspaceId: r.string('workspace_id'),
      userId: r.string('user_id'),
      role: r.string('role'),
      active: r.boolean('active'),
    );
  }

  final String tenantId;
  final String workspaceId;
  final String userId;
  final String role;
  final bool active;
}

/// One of the caller's own active memberships in their tenant, from
/// `GET /api/v1/me/workspace-memberships` (docs/identity.md, My workspaces).
class CallerMembership {
  const CallerMembership({
    required this.tenantId,
    required this.workspaceId,
    required this.role,
    this.workspaceName,
  });

  factory CallerMembership.fromJson(JsonMap json, [String path = '']) {
    final r = JsonReader(json, path);
    final name = r.optString('workspace_name')?.trim();
    return CallerMembership(
      tenantId: r.string('tenant_id'),
      workspaceId: r.string('workspace_id'),
      role: r.string('role'),
      workspaceName: name == null || name.isEmpty ? null : name,
    );
  }

  final String tenantId;
  final String workspaceId;
  final String role;

  /// The workspace's display name, or null when the API could not read one.
  final String? workspaceName;
}

class OnboardingStatus {
  const OnboardingStatus({
    required this.complete,
    required this.modelConfigured,
    this.tenant,
    this.workspace,
    this.membership,
  });

  factory OnboardingStatus.fromJson(JsonMap json) {
    final r = JsonReader(json);
    final tenant = r.optObject('tenant');
    final workspace = r.optObject('workspace');
    final membership = r.optObject('membership');
    return OnboardingStatus(
      complete: r.boolean('complete'),
      modelConfigured: r.boolean('model_configured'),
      tenant: tenant == null
          ? null
          : TenantSummary.fromJson(tenant.json, tenant.path),
      workspace: workspace == null
          ? null
          : WorkspaceSummary.fromJson(workspace.json, workspace.path),
      membership: membership == null
          ? null
          : MembershipSummary.fromJson(membership.json, membership.path),
    );
  }

  final bool complete;
  final bool modelConfigured;
  final TenantSummary? tenant;
  final WorkspaceSummary? workspace;
  final MembershipSummary? membership;
}

/// Whether anything answers agent tasks when the person skips connecting a
/// model during first run (docs/mobile.md#first-run).
class ModelAvailability {
  const ModelAvailability(
      {required this.workspaceConfigured, this.serverModel});

  /// The workspace already has a usable saved model.
  final bool workspaceConfigured;

  /// The model the API server is configured with, or null when the server has
  /// none (only the built-in mock planner).
  final String? serverModel;

  /// Skipping the model step is offered only when one of them answers.
  bool get canSkip => workspaceConfigured || serverModel != null;
}

/// Model routing for the `anthropic` provider (docs/model-gateway.md#model-routing):
/// quick replies (chat, voice, memory) use [fastModel], planning [capableModel].
/// Null means not set: the saved model serves that route and no effort is sent.
class ModelRouting {
  const ModelRouting({
    this.fastModel,
    this.capableModel,
    this.fastEffort,
    this.capableEffort,
  });

  factory ModelRouting.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return ModelRouting(
      fastModel: r.optString('fast_model'),
      capableModel: r.optString('capable_model'),
      fastEffort: r.optString('fast_effort'),
      capableEffort: r.optString('capable_effort'),
    );
  }

  final String? fastModel;
  final String? capableModel;
  final String? fastEffort;
  final String? capableEffort;

  static String? _clean(String? value) {
    final text = value?.trim() ?? '';
    return text.isEmpty ? null : text;
  }

  /// The PUT body fields; empty values are sent as null ("not set").
  JsonMap toJson() => {
        'fast_model': _clean(fastModel),
        'capable_model': _clean(capableModel),
        'fast_effort': _clean(fastEffort),
        'capable_effort': _clean(capableEffort),
      };
}

class ModelConfiguration {
  const ModelConfiguration({
    required this.provider,
    required this.model,
    required this.baseUrl,
    required this.credentialConfigured,
    required this.updatedAt,
    this.credentialHint,
    this.routing = const ModelRouting(),
  });

  factory ModelConfiguration.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return ModelConfiguration(
      provider: r.string('provider'),
      model: r.string('model'),
      baseUrl: r.string('base_url'),
      credentialConfigured: r.boolean('credential_configured'),
      credentialHint: r.optString('credential_hint'),
      updatedAt: r.date('updated_at').toUtc(),
      routing: ModelRouting.fromJson(json),
    );
  }

  final String provider;
  final String model;
  final String baseUrl;
  final bool credentialConfigured;
  final String? credentialHint;
  final DateTime updatedAt;
  final ModelRouting routing;

  /// The model that answers chat, voice and memory.
  String get fastModel =>
      provider == 'anthropic' ? (routing.fastModel ?? model) : model;

  /// The model that plans tasks and agent runs.
  String get capableModel =>
      provider == 'anthropic' ? (routing.capableModel ?? model) : model;
}

class NotificationPreferences {
  const NotificationPreferences({
    this.taskCompleted = true,
    this.approvalRequired = true,
    this.runFailed = true,
    this.automationFailed = true,
    this.emailEnabled = false,
    this.desktopEnabled = true,
  });

  factory NotificationPreferences.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return NotificationPreferences(
      taskCompleted: r.boolean('task_completed'),
      approvalRequired: r.boolean('approval_required'),
      runFailed: r.boolean('run_failed'),
      automationFailed: r.boolean('automation_failed'),
      emailEnabled: r.boolean('email_enabled'),
      desktopEnabled: r.boolean('desktop_enabled'),
    );
  }

  final bool taskCompleted;
  final bool approvalRequired;
  final bool runFailed;
  final bool automationFailed;
  final bool emailEnabled;
  final bool desktopEnabled;

  JsonMap toJson() => {
        'task_completed': taskCompleted,
        'approval_required': approvalRequired,
        'run_failed': runFailed,
        'automation_failed': automationFailed,
        'email_enabled': emailEnabled,
        'desktop_enabled': desktopEnabled,
      };
}

/// "Remember things from my chats" (`GET/PUT /api/v1/me/preferences`,
/// docs/memory.md#from-conversations): the caller's own switch, and whether
/// chat memory is on for them and, if not, why.
class ChatMemoryPreference {
  const ChatMemoryPreference(
      {required this.rememberConversations, required this.state});

  factory ChatMemoryPreference.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return ChatMemoryPreference(
      rememberConversations: r.boolean('remember_conversations'),
      state: r.optString('conversation_memory') ?? 'unknown',
    );
  }

  final bool rememberConversations;

  /// `on`, `off_by_user`, `off_on_server`, `off_for_organization` or
  /// `not_for_role`.
  final String state;

  static const _kept =
      'Memories already saved from your chats stay until you delete them.';

  /// Short status for the badge.
  String get badge => switch (state) {
        'on' => 'On',
        'off_by_user' => 'Off',
        'off_on_server' => 'Off on this server',
        'off_for_organization' => 'Off for your organization',
        'not_for_role' => 'Not remembering',
        _ => 'Unknown',
      };

  /// What happens now and, when someone else keeps it off, who can change it.
  String get explanation => switch (state) {
        'on' =>
          'The Assistant remembers lasting facts you state in your chats (your name, role or how you like answers) and uses them in later answers. Only you can see them.',
        'off_by_user' =>
          'Nothing new is remembered from your chats and no memories are used in your answers. $_kept',
        'off_on_server' =>
          'The server administrator has turned chat memory off for everyone. Your choice is saved and applies if it is turned back on. $_kept',
        'off_for_organization' =>
          'Your organization\'s memory policy does not allow memories from chats. An organization administrator can allow it under Governance. Your choice is saved and applies if they do. $_kept',
        'not_for_role' =>
          'Your role in this workspace cannot save memories, so nothing new is remembered from your chats. A workspace owner can change your role.',
        _ => 'The server reported a state this version does not know.',
      };
}
