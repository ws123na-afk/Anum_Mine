/// Cards on assistant turns (docs/conversations.md#tools-in-chat): a chat
/// question that needed data or an action ran as a task, and the card holds
/// its results, the approval it paused for, or why it failed. Mirrors
/// `apps/web/src/lib/chat.ts`. Pure Dart.
library;

import '../../data/api_models.dart';

enum ChatCardState { running, results, approval, failed, cancelled, denied }

enum ChatCardCategory { email, whatsapp, web, files, action }

ChatCardState? _state(String? value) => switch (value) {
      'running' => ChatCardState.running,
      'results' => ChatCardState.results,
      'approval' => ChatCardState.approval,
      'failed' => ChatCardState.failed,
      'cancelled' => ChatCardState.cancelled,
      'denied' => ChatCardState.denied,
      _ => null,
    };

ChatCardCategory? _category(String? value) => switch (value) {
      'email' => ChatCardCategory.email,
      'whatsapp' => ChatCardCategory.whatsapp,
      'web' => ChatCardCategory.web,
      'files' => ChatCardCategory.files,
      'action' => ChatCardCategory.action,
      _ => null,
    };

/// One result row: `id` opens it in its view; `url` is set for web results.
class ChatCardItem {
  const ChatCardItem({
    required this.kind,
    required this.title,
    this.id,
    this.subtitle,
    this.detail,
    this.at,
    this.badges = const [],
    this.url,
  });

  factory ChatCardItem.fromReader(JsonReader r) => ChatCardItem(
        kind: r.string('kind'),
        id: r.optString('id'),
        title: r.optString('title') ?? '',
        subtitle: r.optString('subtitle'),
        detail: r.optString('detail'),
        at: r.optDate('at'),
        badges: r.optStrings('badges') ?? const [],
        url: r.optString('url'),
      );

  /// `email`, `whatsapp`, `web`, `file` or `memory`.
  final String kind;
  final String? id;
  final String title;
  final String? subtitle;
  final String? detail;
  final DateTime? at;
  final List<String> badges;
  final String? url;

  /// A web link the card may open: http(s) only.
  Uri? get webLink {
    final value = url;
    if (kind != 'web' || value == null) return null;
    final uri = Uri.tryParse(value);
    return uri != null && (uri.scheme == 'https' || uri.scheme == 'http')
        ? uri
        : null;
  }
}

/// The approval a card shows, with the payload hash Approve must send back.
class ChatCardApproval {
  const ChatCardApproval({
    required this.id,
    required this.action,
    required this.riskLevel,
    required this.status,
    required this.reason,
    this.payloadHash,
    this.arguments = const {},
    this.target,
    this.expiresAt,
    this.canDecide = false,
  });

  factory ChatCardApproval.fromReader(JsonReader r) => ChatCardApproval(
        id: r.string('id'),
        action: r.optString('action') ?? '',
        riskLevel: r.optString('risk_level') ?? 'high',
        status: r.optString('status') ?? 'pending',
        reason: r.optString('reason') ?? '',
        payloadHash: r.optString('payload_hash'),
        arguments: r.optMap('arguments') ?? const {},
        target: r.optString('target'),
        expiresAt: r.optDate('expires_at'),
        canDecide: r.optBool('can_decide') ?? false,
      );

  final String id;
  final String action;
  final String riskLevel;
  final String status;
  final String reason;
  final String? payloadHash;
  final Map<String, Object?> arguments;
  final String? target;
  final DateTime? expiresAt;
  final bool canDecide;

  bool get pending => status == 'pending';

  ChatCardApproval withStatus(String value) => ChatCardApproval(
        id: id,
        action: action,
        riskLevel: riskLevel,
        status: value,
        reason: reason,
        payloadHash: payloadHash,
        arguments: arguments,
        target: target,
        expiresAt: expiresAt,
        canDecide: canDecide,
      );
}

class ChatCard {
  const ChatCard({
    required this.state,
    required this.category,
    this.taskId,
    this.tool,
    this.summary = '',
    this.items = const [],
    this.total = 0,
    this.approval,
    this.errorCode,
  });

  /// The card carried by [value], or null when there is none or this app
  /// cannot read it (the turn's text still shows).
  static ChatCard? tryParse(Object? value) {
    if (value is! Map) return null;
    try {
      final r = JsonReader.of(value, 'card');
      final state = _state(r.optString('state'));
      final category = _category(r.optString('category'));
      if (state == null || category == null) return null;
      final approval = r.optObject('approval');
      return ChatCard(
        state: state,
        category: category,
        taskId: r.optString('task_id'),
        tool: r.optString('tool'),
        summary: r.optString('summary') ?? '',
        items: r.optObjects('items', ChatCardItem.fromReader),
        total: r.optInt('total') ?? 0,
        approval:
            approval == null ? null : ChatCardApproval.fromReader(approval),
        errorCode: r.optString('error_code'),
      );
    } on FormatException {
      return null;
    }
  }

  final ChatCardState state;
  final ChatCardCategory category;
  final String? taskId;
  final String? tool;
  final String summary;
  final List<ChatCardItem> items;
  final int total;
  final ChatCardApproval? approval;
  final String? errorCode;

  ChatCard copyWith({
    ChatCardState? state,
    String? summary,
    ChatCardApproval? approval,
  }) =>
      ChatCard(
        state: state ?? this.state,
        category: category,
        taskId: taskId,
        tool: tool,
        summary: summary ?? this.summary,
        items: items,
        total: total,
        approval: approval ?? this.approval,
        errorCode: errorCode,
      );

  /// The card after an Approve or Reject: the decision's task, run and
  /// approval (`POST /api/v1/approvals/{id}/approve|reject`).
  ChatCard afterDecision(JsonMap response) {
    final r = JsonReader(response);
    final task = r.optObject('task');
    final run = r.optObject('run');
    final decided = r.optObject('approval')?.optString('status');
    final approval =
        decided == null ? this.approval : this.approval?.withStatus(decided);
    final status = task?.optString('status');
    String? failure() {
      final steps = run?.optList('steps') ?? const [];
      for (final step in steps.reversed) {
        if (step is Map &&
            step['type'] == 'tool_result' &&
            step['summary'] is String) {
          return step['summary'] as String;
        }
      }
      return run?.optString('result');
    }

    return switch (status) {
      'completed' => copyWith(
          state: ChatCardState.results,
          summary: run?.optString('result') ?? summary,
          approval: approval),
      'failed' => copyWith(
          state: ChatCardState.failed,
          summary: failure() ?? summary,
          approval: approval),
      'cancelled' =>
        copyWith(state: ChatCardState.cancelled, approval: approval),
      'waiting_approval' =>
        copyWith(state: ChatCardState.approval, approval: approval),
      _ => copyWith(state: ChatCardState.running, approval: approval),
    };
  }
}

/// Where a card's link goes; the workspace opens the matching screen.
enum ChatTargetKind {
  email,
  whatsapp,
  file,
  memory,
  task,
  inbox,
  whatsappInbox,
  files,
  approvals
}

class ChatTarget {
  const ChatTarget(this.kind, [this.id]);
  final ChatTargetKind kind;
  final String? id;
}
