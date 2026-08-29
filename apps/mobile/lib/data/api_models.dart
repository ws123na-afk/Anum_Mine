typedef JsonMap = Map<String, Object?>;

class TenantContext {
  const TenantContext({
    required this.tenantId,
    required this.workspaceId,
    required this.userId,
    required this.roles,
  });

  factory TenantContext.fromJson(JsonMap json) => TenantContext(
        tenantId: json['tenant_id']! as String,
        workspaceId: json['workspace_id']! as String,
        userId: json['user_id']! as String,
        roles: (json['roles']! as List<Object?>).cast<String>(),
      );

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

class LocalSession {
  const LocalSession({
    required this.accessToken,
    required this.tokenType,
    required this.expiresAt,
    required this.context,
  });

  factory LocalSession.fromJson(JsonMap json) => LocalSession(
        accessToken: json['access_token']! as String,
        tokenType: json['token_type']! as String,
        expiresAt: DateTime.parse(json['expires_at']! as String).toUtc(),
        context: TenantContext.fromJson(json['context']! as JsonMap),
      );

  final String accessToken;
  final String tokenType;
  final DateTime expiresAt;
  final TenantContext context;

  bool get isExpired => !expiresAt.isAfter(DateTime.now().toUtc());

  JsonMap toJson() => {
        'access_token': accessToken,
        'token_type': tokenType,
        'expires_at': expiresAt.toIso8601String(),
        'context': context.toJson(),
      };
}

class TenantSummary {
  const TenantSummary({required this.id, required this.name});

  factory TenantSummary.fromJson(JsonMap json) => TenantSummary(
        id: json['id']! as String,
        name: json['name']! as String,
      );

  final String id;
  final String name;
}

class WorkspaceSummary {
  const WorkspaceSummary({
    required this.id,
    required this.tenantId,
    required this.name,
  });

  factory WorkspaceSummary.fromJson(JsonMap json) => WorkspaceSummary(
        id: json['id']! as String,
        tenantId: json['tenant_id']! as String,
        name: json['name']! as String,
      );

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

  factory MembershipSummary.fromJson(JsonMap json) => MembershipSummary(
        tenantId: json['tenant_id']! as String,
        workspaceId: json['workspace_id']! as String,
        userId: json['user_id']! as String,
        role: json['role']! as String,
        active: json['active']! as bool,
      );

  final String tenantId;
  final String workspaceId;
  final String userId;
  final String role;
  final bool active;
}

class OnboardingStatus {
  const OnboardingStatus({
    required this.complete,
    required this.modelConfigured,
    this.tenant,
    this.workspace,
    this.membership,
  });

  factory OnboardingStatus.fromJson(JsonMap json) => OnboardingStatus(
        complete: json['complete']! as bool,
        modelConfigured: json['model_configured']! as bool,
        tenant: json['tenant'] == null
            ? null
            : TenantSummary.fromJson(json['tenant']! as JsonMap),
        workspace: json['workspace'] == null
            ? null
            : WorkspaceSummary.fromJson(json['workspace']! as JsonMap),
        membership: json['membership'] == null
            ? null
            : MembershipSummary.fromJson(json['membership']! as JsonMap),
      );

  final bool complete;
  final bool modelConfigured;
  final TenantSummary? tenant;
  final WorkspaceSummary? workspace;
  final MembershipSummary? membership;
}

class ModelConfiguration {
  const ModelConfiguration({
    required this.provider,
    required this.model,
    required this.baseUrl,
    required this.credentialConfigured,
    required this.updatedAt,
    this.credentialHint,
  });

  factory ModelConfiguration.fromJson(JsonMap json) => ModelConfiguration(
        provider: json['provider']! as String,
        model: json['model']! as String,
        baseUrl: json['base_url']! as String,
        credentialConfigured: json['credential_configured']! as bool,
        credentialHint: json['credential_hint'] as String?,
        updatedAt: DateTime.parse(json['updated_at']! as String).toUtc(),
      );

  final String provider;
  final String model;
  final String baseUrl;
  final bool credentialConfigured;
  final String? credentialHint;
  final DateTime updatedAt;
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

  factory NotificationPreferences.fromJson(JsonMap json) => NotificationPreferences(
        taskCompleted: json['task_completed']! as bool,
        approvalRequired: json['approval_required']! as bool,
        runFailed: json['run_failed']! as bool,
        automationFailed: json['automation_failed']! as bool,
        emailEnabled: json['email_enabled']! as bool,
        desktopEnabled: json['desktop_enabled']! as bool,
      );

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
