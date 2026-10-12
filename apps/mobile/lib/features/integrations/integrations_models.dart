import '../../data/json_read.dart';

/// One integration as `GET /api/v1/integrations` reports it for this
/// workspace (docs/tools-and-integrations.md): the operator's configuration,
/// the workspace's switch, the latest health check and, for tools that take
/// one, whether the workspace stored its own credential (never its value).
class Integration {
  const Integration({
    required this.id,
    required this.name,
    required this.kind,
    required this.status,
    required this.endpoint,
    required this.detail,
    this.enabled = true,
    this.available = true,
    this.latencyMs,
    this.remediation,
    this.environmentVariable,
    this.checkedAt,
    this.lastError,
    this.lastErrorAt,
    this.credentialSource = 'none',
    this.credentialConfigured = false,
    this.scopes = const [],
    this.acceptsWorkspaceCredential = false,
    this.workspaceCredentialHint,
    this.workspaceCredentialUpdatedAt,
  });

  final String id, name, kind, endpoint, detail;

  /// `connected`, `degraded`, `configured` or `disabled`.
  final String status;

  /// The workspace uses it (an owner's switch over the operator's default).
  final bool enabled;

  /// The operator configured it for this deployment; when false no
  /// workspace switch can turn it on.
  final bool available;
  final int? latencyMs;
  final String? remediation, environmentVariable;
  final DateTime? checkedAt, lastErrorAt;
  final String? lastError;

  /// Where its credential comes from: `environment`, `workspace`, `oauth`
  /// or `none`.
  final String credentialSource;
  final bool credentialConfigured;
  final List<String> scopes;
  final bool acceptsWorkspaceCredential;

  /// The last characters of the stored workspace credential, when one is.
  final String? workspaceCredentialHint;
  final DateTime? workspaceCredentialUpdatedAt;

  bool get hasWorkspaceCredential => workspaceCredentialHint != null;

  /// Connected for the Connected tab: in use and answering (a degraded one
  /// is connected but needs attention).
  bool get connected =>
      available && enabled && (status == 'connected' || status == 'degraded');
  bool get degraded => status == 'degraded';

  /// Email providers connect per person, through the inbox (docs/email.md).
  bool get isEmail => kind == 'email';

  factory Integration.fromJson(JsonReader j) {
    final credentials = j.optObject('credentials');
    final workspace = j.optObject('workspace_credential');
    final stored = workspace?.optBool('configured') ?? false;
    return Integration(
      id: j.string('id'),
      name: j.string('name'),
      kind: j.optString('kind') ?? 'tool',
      status: j.optString('status') ?? 'disabled',
      endpoint: j.optString('endpoint') ?? '',
      detail: j.optString('detail') ?? '',
      enabled: j.optBool('enabled') ?? true,
      available: j.optBool('available') ?? true,
      latencyMs: j.optInt('latency_ms'),
      remediation: j.optString('remediation'),
      environmentVariable: j.optString('environment_variable'),
      checkedAt: j.optDate('checked_at')?.toLocal(),
      lastError: j.optString('last_error'),
      lastErrorAt: j.optDate('last_error_at')?.toLocal(),
      credentialSource: credentials?.optString('source') ?? 'none',
      credentialConfigured: credentials?.optBool('configured') ?? false,
      scopes: credentials?.optStrings('scopes') ?? const [],
      acceptsWorkspaceCredential:
          j.optBool('accepts_workspace_credential') ?? false,
      workspaceCredentialHint:
          stored ? (workspace?.optString('hint') ?? '') : null,
      workspaceCredentialUpdatedAt:
          stored ? workspace?.optDate('updated_at')?.toLocal() : null,
    );
  }
}

/// The tabs of the Integrations list (mockup 12).
enum IntegrationTab { all, connected, available }

bool integrationInTab(Integration integration, IntegrationTab tab) =>
    switch (tab) {
      IntegrationTab.all => true,
      IntegrationTab.connected => integration.connected,
      IntegrationTab.available => !integration.connected,
    };

/// What the row's action does for this person.
enum IntegrationAction {
  /// In use and healthy: nothing to do.
  connected,

  /// In use but its last check failed: open it to test or fix.
  attention,

  /// An owner can turn it on for the workspace, test it or store its key.
  connect,

  /// Email: each person connects their own mailbox in the inbox.
  connectMailbox,

  /// The server's operator must configure it; nothing to do in the app.
  operator,

  /// Only a workspace owner can turn it on.
  ownerOnly,
}

IntegrationAction integrationAction(Integration i, {required bool owner}) {
  if (i.connected) {
    return i.degraded
        ? IntegrationAction.attention
        : IntegrationAction.connected;
  }
  if (!i.available) return IntegrationAction.operator;
  if (i.isEmail) return IntegrationAction.connectMailbox;
  return owner ? IntegrationAction.connect : IntegrationAction.ownerOnly;
}

/// Longest credential the API accepts, and its shortest.
const integrationSecretMin = 12;
const integrationSecretMax = 4096;

/// Why [secret] cannot be stored, or null when the API will accept it
/// (12-4096 printable ASCII characters without spaces).
String? integrationSecretProblem(String secret) {
  if (secret.length < integrationSecretMin) return 'short';
  if (secret.length > integrationSecretMax) return 'long';
  if (!RegExp(r'^[\x21-\x7e]+$').hasMatch(secret)) return 'characters';
  return null;
}
