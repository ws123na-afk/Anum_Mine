import '../../data/json_read.dart';
import 'privacy_strings.dart';

/// `GET /api/v1/privacy` (docs/privacy.md): counts of what ANUM stores about
/// the caller in this workspace, retention, provider and the policy link.
class PrivacyCount {
  const PrivacyCount(this.key, this.label, this.value);
  final String key;
  final String label;
  final int value;
}

const _privateLabels = <String, String>{
  'conversations': 'Assistant conversations',
  'conversation_turns': 'Messages in those conversations',
  'private_memories': 'Private memories (from your chats)',
  'retrieval_index_entries': 'Search index entries for those memories',
  'voice_sessions': 'Voice sessions',
  'voice_transcript_segments': 'Voice transcript lines',
  'notification_preferences': 'Notification preferences',
  'user_preferences': 'Your preferences (all workspaces)',
  'push_devices': 'Phones registered for push',
  'learning_signals': 'Feedback ANUM learned from',
  'learned_items': 'Learned style, preferences, routines and notes',
  'favorites': 'Your favorites',
  'private_collections': 'Private knowledge collections',
  'private_reports': 'Private reports',
  'email_mailboxes': 'Private mailboxes you connected',
  'email_messages': 'Email details synced from those mailboxes',
  'email_drafts': 'Email drafts you never sent',
  'team_notifications': 'Your team notifications',
  'team_profile': 'Your team profile (name, email, title)',
  'private_agents': 'Your private agents',
  'private_agent_memories': 'Memories of your private agents',
  'finance_records': 'Finance records',
  'app_calls':
      'Your calls with teammates (the other person keeps theirs, showing you as a deleted user)',
  'app_call_signals': 'Call setup messages',
};

const _sharedLabels = <String, String>{
  'tasks_created': 'Tasks you created (with their runs)',
  'approvals_decided': 'Approvals you decided',
  'shared_memories_created': 'Shared memories you added',
  'files_uploaded': 'Files you uploaded',
  'email_mailboxes_connected': 'Workspace mailboxes you connected',
  'email_drafts_sent': 'Emails you sent or sent for approval',
  'whatsapp_messages_sent': 'WhatsApp messages you sent',
  'autonomy_actions_requested': 'Actions ANUM took for you',
  'work_items_created': 'Work items you created',
  'work_items_assigned': 'Work items assigned to you',
  'tyre_connections_created': 'Tyre data connection you set up',
  'tyre_reports_created': 'Tyre reports you created',
  'commerce_connectors_created': 'Sales channels you connected',
  'marketing_accounts_connected': 'Social accounts you connected',
  'marketing_campaigns_created': 'Marketing campaigns you created',
  'calendar_events_created': 'Calendar events you created',
  'automation_triggers_created': 'Automation triggers you added',
  'shared_agents_owned': 'Agents you shared with the workspace',
  'shared_agent_memories': 'Memories of the agents you shared',
};

List<PrivacyCount> _counts(JsonReader json, Map<String, String> labels) => [
      for (final entry in labels.entries)
        PrivacyCount(entry.key, entry.value, json.optInt(entry.key) ?? 0)
    ];

int totalOf(List<PrivacyCount> counts) =>
    counts.fold(0, (sum, item) => sum + item.value);

class PrivacySummary {
  const PrivacySummary({
    required this.userId,
    required this.workspaceId,
    required this.role,
    required this.private,
    required this.shared,
    required this.conversationDays,
    required this.voiceTranscriptDays,
    required this.privateMemoryRetention,
    required this.taskMemoryRetention,
    required this.auditRetention,
    required this.provider,
    required this.memoryExtraction,
    required this.conversationMemory,
    required this.policyUrl,
    required this.confirmation,
    required this.exportMaxBytes,
    required this.accountNote,
    required this.sharedNote,
  });

  factory PrivacySummary.fromJson(Map<String, Object?> body) {
    final json = JsonReader(body);
    final retention = json.object('retention');
    final provider = json.object('model_provider');
    final url = json.optString('privacy_policy_url');
    return PrivacySummary(
      userId: json.string('user_id'),
      workspaceId: json.string('workspace_id'),
      role: json.optString('role'),
      private: _counts(json.object('private'), _privateLabels),
      shared: _counts(json.object('shared'), _sharedLabels),
      conversationDays: retention.integer('conversation_days'),
      voiceTranscriptDays: retention.optInt('voice_transcript_days') ?? 30,
      privateMemoryRetention: retention.optString('private_memories') ?? '',
      taskMemoryRetention: retention.optString('task_memories') ?? '',
      auditRetention: retention.optString('audit_records') ?? '',
      provider:
          provider.optString('workspace') ?? provider.string('deployment'),
      memoryExtraction:
          provider.optBool('conversation_memory_enabled') ?? false,
      conversationMemory:
          provider.optString('conversation_memory') ?? 'unknown',
      policyUrl: url == null || url.isEmpty ? null : url,
      confirmation: json.optString('delete_confirmation') ?? 'DELETE MY DATA',
      exportMaxBytes: json.optInt('export_max_bytes') ?? 0,
      accountNote: json.optString('account_note') ?? '',
      sharedNote: json.optString('shared_note') ?? '',
    );
  }

  final String userId;
  final String workspaceId;
  final String? role;
  final List<PrivacyCount> private;
  final List<PrivacyCount> shared;
  final int conversationDays;
  final int voiceTranscriptDays;
  final String privateMemoryRetention;
  final String taskMemoryRetention;
  final String auditRetention;
  final String provider;

  /// The deployment-wide switch (ANUM_CONVERSATION_MEMORY_ENABLED) only.
  final bool memoryExtraction;

  /// What applies to the caller (`model_provider.conversation_memory`, the
  /// same value as `GET /api/v1/me/preferences`): on, off_by_user,
  /// off_on_server, off_for_organization or not_for_role.
  final String conversationMemory;
  final String? policyUrl;
  final String confirmation;
  final int exportMaxBytes;
  final String accountNote;
  final String sharedNote;

  /// Retention as plain sentences, from the server's configuration.
  List<(String, String)> get retention => retentionIn(PrivacyStrings.english);

  List<(String, String)> retentionIn(PrivacyStrings s) => [
        (
          s.t('ret.conversations'),
          conversationDays > 0
              ? s.t('ret.conversationsDays',
                  {'days': s.count('days', conversationDays)})
              : s.t('ret.conversationsKept')
        ),
        (
          s.t('ret.voice'),
          s.t('ret.voiceText', {'days': s.count('days', voiceTranscriptDays)})
        ),
        (s.t('ret.private'), privateMemoryRetention),
        (s.t('ret.task'), taskMemoryRetention),
        (s.t('ret.audit'), auditRetention),
      ];

  String get providerSentence => providerSentenceIn(PrivacyStrings.english);

  String providerSentenceIn(PrivacyStrings s) =>
      s.t('provider', {'provider': providerLabel(provider, s)});

  /// The caller turned "Remember things from my chats" off: offer the switch.
  bool get offerChatMemorySwitch => conversationMemory == 'off_by_user';

  /// What happens to the caller's chats after a reply. The post-reply
  /// extraction call runs only when memory is `on`; recall stops only when the
  /// caller or the server turned memory off.
  String get memorySentence => memorySentenceIn(PrivacyStrings.english);

  String memorySentenceIn(PrivacyStrings s) => switch (conversationMemory) {
        'on' ||
        'off_by_user' ||
        'off_on_server' ||
        'off_for_organization' ||
        'not_for_role' =>
          s.t('memory.$conversationMemory', {'recall': s.t('memory.recall')}),
        _ => s.t('memory.unknown'),
      };

  bool matches(String typed) => typed.trim() == confirmation;
}

String providerLabel(String provider,
        [PrivacyStrings s = PrivacyStrings.english]) =>
    switch (provider.toLowerCase().replaceAll('_', '-')) {
      'anthropic' => s.t('provider.anthropic'),
      'openai-compatible' => s.t('provider.openai'),
      'ollama' => s.t('provider.ollama'),
      'mock' => s.t('provider.mock'),
      _ => provider,
    };

/// `POST /api/v1/privacy/delete`: what went, what stayed.
class PrivacyDeleteResult {
  const PrivacyDeleteResult(
      {required this.deleted,
      required this.kept,
      required this.accountNote,
      this.notes = const []});

  factory PrivacyDeleteResult.fromJson(Map<String, Object?> body) {
    final json = JsonReader(body);
    return PrivacyDeleteResult(
      deleted: _counts(json.object('deleted'), _privateLabels),
      kept: _counts(json.object('kept'), _sharedLabels),
      accountNote: json.optString('account_note') ?? '',
      notes: json.optStrings('notes') ?? const [],
    );
  }

  final List<PrivacyCount> deleted;
  final List<PrivacyCount> kept;
  final String accountNote;

  /// The server's own sentences about what still needs the caller (e.g. a
  /// mailbox provider where ANUM could not withdraw its access).
  final List<String> notes;

  String get sentence => sentenceIn(PrivacyStrings.english);

  String sentenceIn(PrivacyStrings s) {
    final removed = totalOf(deleted), stayed = totalOf(kept);
    final head = removed > 0
        ? s.count('result.deleted', removed)
        : s.t('result.nothing');
    final base = stayed > 0 ? '$head ${s.count('result.stays', stayed)}' : head;
    return [base, ...notes].join(' ');
  }
}

String formatBytes(int value) => value < 1024
    ? '$value B'
    : value < 1024 * 1024
        ? '${(value / 1024).toStringAsFixed(1)} KB'
        : '${(value / (1024 * 1024)).toStringAsFixed(1)} MB';
