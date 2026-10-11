import '../../data/api_models.dart';

/// Whether the server can send phone push (`GET /api/v1/push/status`,
/// docs/notifications.md).
class PushServerStatus {
  const PushServerStatus({
    required this.available,
    required this.message,
    this.projectId,
  });

  factory PushServerStatus.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return PushServerStatus(
      available: r.boolean('available'),
      message: r.optString('message') ?? '',
      projectId: r.optString('project_id'),
    );
  }

  final bool available;
  final String message;
  final String? projectId;
}

/// One of the caller's phones registered for push in this workspace. The
/// server never returns the push token, only its last four characters.
class PushDevice {
  const PushDevice({
    required this.id,
    required this.platform,
    required this.tokenHint,
    required this.createdAt,
    this.label,
    this.lastSentAt,
  });

  factory PushDevice.fromJson(JsonMap json, [String path = '']) {
    final r = JsonReader(json, path);
    return PushDevice(
      id: r.string('id'),
      platform: r.string('platform'),
      label: r.optString('label'),
      tokenHint: r.string('token_hint'),
      createdAt: r.date('created_at'),
      lastSentAt: r.optDate('last_sent_at'),
    );
  }

  final String id;
  final String platform;
  final String? label;
  final String tokenHint;
  final DateTime createdAt;
  final DateTime? lastSentAt;

  String get platformName => platform == 'ios' ? 'iPhone or iPad' : 'Android';

  /// "Android phone, ends in 1a2b": enough to tell phones apart.
  String get title {
    final name = (label?.trim().isNotEmpty ?? false)
        ? label!.trim()
        : '$platformName phone';
    return '$name, ends in $tokenHint';
  }
}

enum PushDestinationKind { approval, task, developer, tyreAlerts }

/// Where a tapped notification leads. A push carries ids only
/// (`anum_kind`, `workspace_id`, `approval_id`, `task_id`,
/// `developer_request_id`); everything else is loaded from the API.
class PushDestination {
  const PushDestination({
    required this.kind,
    required this.workspaceId,
    this.tenantId,
    this.taskId,
    this.approvalId,
    this.requestId,
  });

  final PushDestinationKind kind;
  final String workspaceId;
  final String? tenantId;
  final String? taskId;
  final String? approvalId;
  final String? requestId;

  /// Null when the data is not an ANUM push (or names no workspace).
  static PushDestination? fromData(Map<String, Object?> data) {
    String? text(String key) {
      final value = data[key];
      return value is String && value.isNotEmpty ? value : null;
    }

    final workspaceId = text('workspace_id');
    final kind = switch (text('anum_kind')) {
      'approval_required' => PushDestinationKind.approval,
      'task_completed' || 'run_failed' => PushDestinationKind.task,
      'developer_ready' || 'developer_failed' => PushDestinationKind.developer,
      'tyre_alerts' => PushDestinationKind.tyreAlerts,
      _ => null,
    };
    if (kind == null || workspaceId == null) return null;
    final taskId = text('task_id');
    if (kind == PushDestinationKind.task && taskId == null) return null;
    return PushDestination(
      kind: kind,
      workspaceId: workspaceId,
      tenantId: text('tenant_id'),
      taskId: taskId,
      approvalId: text('approval_id'),
      requestId: text('developer_request_id'),
    );
  }

  /// The app's named route for it (AnumRoutes in src/anum_app.dart).
  String get route => switch (kind) {
        PushDestinationKind.approval => '/approvals',
        PushDestinationKind.task => '/tasks/${Uri.encodeComponent(taskId!)}',
        PushDestinationKind.developer => '/developer',
        PushDestinationKind.tyreAlerts => '/tyres/alerts',
      };
}

/// A push that arrived while the app was open, shown in-app instead of by
/// the system.
class PushNotice {
  const PushNotice({required this.title, required this.body, this.destination});
  final String title;
  final String body;
  final PushDestination? destination;
}
