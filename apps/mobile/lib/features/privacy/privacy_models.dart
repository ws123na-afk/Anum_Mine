import '../../data/json_read.dart';

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
  List<(String, String)> get retention => [
        (
          'Assistant conversations',
          conversationDays > 0
              ? 'Deleted after ${_days(conversationDays)} without activity, or when you delete them.'
              : 'Kept until you delete them.'
        ),
        (
          'Voice transcripts',
          'You choose per session: removed when the session ends, after ${_days(voiceTranscriptDays)}, or kept until you delete them.'
        ),
        ('Private memories', privateMemoryRetention),
        ('Task memories', taskMemoryRetention),
        ('Audit log', auditRetention),
      ];

  String get providerSentence =>
      'To produce answers, your messages, voice questions and task prompts are sent to ${providerLabel(provider)}.';

  /// The caller turned "Remember things from my chats" off: offer the switch.
  bool get offerChatMemorySwitch => conversationMemory == 'off_by_user';

  /// What happens to the caller's chats after a reply. The post-reply
  /// extraction call runs only when memory is `on`; recall stops only when the
  /// caller or the server turned memory off.
  String get memorySentence => switch (conversationMemory) {
        'on' =>
          'After each reply, a second short call to the same provider picks out lasting facts you stated (such as your name, role or how you like answers) and saves them as private memories only you can see. Memories that match your later questions are added to what is sent to the model.',
        'off_by_user' =>
          'You turned off Remember things from my chats, so no facts are picked out of your chats and no memories are added to your questions. Memories already saved from your chats stay until you delete them.',
        'off_on_server' =>
          'Chat memory is turned off on this server (ANUM_CONVERSATION_MEMORY_ENABLED), so no facts are picked out of your chats and no memories are added to your questions.',
        'off_for_organization' =>
          'Your organization’s memory policy does not allow memories from chats, so no facts are picked out of your chats. $_recallStill An organization administrator can allow the "conversation" source under Governance.',
        'not_for_role' =>
          'Your role in this workspace cannot save memories, so no facts are picked out of your chats. $_recallStill A workspace owner can change your role.',
        _ =>
          'This server did not say whether facts are picked out of your chats. Check Remember things from my chats in Settings, or update the server.',
      };

  bool matches(String typed) => typed.trim() == confirmation;
}

const _recallStill =
    'Memories you can read that match your question (ones you or your workspace saved) are still added to what is sent to the model.';

String _days(int n) => '$n ${n == 1 ? 'day' : 'days'}';

String providerLabel(String provider) =>
    switch (provider.toLowerCase().replaceAll('_', '-')) {
      'anthropic' => 'Anthropic (Claude), an external service',
      'openai-compatible' =>
        'an OpenAI-compatible service, external unless your administrator runs it',
      'ollama' => 'Ollama, a model running on your organization’s own server',
      'mock' =>
        'no model provider (local development mock: nothing leaves the server)',
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

  String get sentence {
    final removed = totalOf(deleted), stayed = totalOf(kept);
    final head = removed > 0
        ? 'Deleted $removed private ${removed == 1 ? 'item' : 'items'}.'
        : 'There was nothing private to delete.';
    final base = stayed > 0
        ? '$head $stayed shared workspace ${stayed == 1 ? 'record stays' : 'records stay'}.'
        : head;
    return [base, ...notes].join(' ');
  }
}

String formatBytes(int value) => value < 1024
    ? '$value B'
    : value < 1024 * 1024
        ? '${(value / 1024).toStringAsFixed(1)} KB'
        : '${(value / (1024 * 1024)).toStringAsFixed(1)} MB';
