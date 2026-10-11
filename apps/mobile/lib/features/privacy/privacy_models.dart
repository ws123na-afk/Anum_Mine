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
};

const _sharedLabels = <String, String>{
  'tasks_created': 'Tasks you created (with their runs)',
  'approvals_decided': 'Approvals you decided',
  'shared_memories_created': 'Shared memories you added',
  'files_uploaded': 'Files you uploaded',
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
  final bool memoryExtraction;
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

  String get providerSentence {
    final memory = memoryExtraction
        ? ' After a reply, a short model call also picks out facts you stated to remember as private memories.'
        : '';
    return 'To produce answers, your messages, voice questions and task prompts are sent to ${providerLabel(provider)}.$memory';
  }

  bool matches(String typed) => typed.trim() == confirmation;
}

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
      {required this.deleted, required this.kept, required this.accountNote});

  factory PrivacyDeleteResult.fromJson(Map<String, Object?> body) {
    final json = JsonReader(body);
    return PrivacyDeleteResult(
      deleted: _counts(json.object('deleted'), _privateLabels),
      kept: _counts(json.object('kept'), _sharedLabels),
      accountNote: json.optString('account_note') ?? '',
    );
  }

  final List<PrivacyCount> deleted;
  final List<PrivacyCount> kept;
  final String accountNote;

  String get sentence {
    final removed = totalOf(deleted), stayed = totalOf(kept);
    final head = removed > 0
        ? 'Deleted $removed private ${removed == 1 ? 'item' : 'items'}.'
        : 'There was nothing private to delete.';
    return stayed > 0
        ? '$head $stayed shared workspace ${stayed == 1 ? 'record stays' : 'records stay'}.'
        : head;
  }
}

String formatBytes(int value) => value < 1024
    ? '$value B'
    : value < 1024 * 1024
        ? '${(value / 1024).toStringAsFixed(1)} KB'
        : '${(value / (1024 * 1024)).toStringAsFixed(1)} MB';
