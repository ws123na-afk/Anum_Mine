import '../../data/json_read.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';

/// The inbox filters the API understands (`GET /api/v1/whatsapp/conversations`).
enum WaFilter {
  all('all'),
  unread('unread'),
  needsReply('needs_reply'),
  awaitingApproval('awaiting_approval');

  const WaFilter(this.api);
  final String api;
}

/// `GET /api/v1/whatsapp/account`: only what the inbox needs (never secrets).
class WaAccount {
  const WaAccount(
      {required this.configured,
      required this.canManage,
      this.displayPhoneNumber,
      this.verifiedName,
      this.status});
  final bool configured, canManage;
  final String? displayPhoneNumber, verifiedName, status;

  factory WaAccount.fromJson(JsonReader j) => WaAccount(
      configured: j.boolean('configured'),
      canManage: j.optBool('can_manage') ?? false,
      displayPhoneNumber: j.optString('display_phone_number'),
      verifiedName: j.optString('verified_name'),
      status: j.optString('status'));
}

/// A suggested reply. [text] came from a model (or rules) that read untrusted
/// customer messages: a draft for a person to check, never sent by itself.
class WaSuggestion {
  const WaSuggestion(
      {required this.text,
      required this.category,
      required this.summary,
      required this.source,
      this.provider,
      this.model,
      this.untrustedMessages = 0});
  final String text, category, summary, source;
  final String? provider, model;
  final int untrustedMessages;
  bool get fromModel => source == 'model';

  factory WaSuggestion.fromJson(JsonReader j) => WaSuggestion(
      text: j.string('text'),
      category: j.string('category'),
      summary: j.string('summary'),
      source: j.string('source'),
      provider: j.optString('provider'),
      model: j.optString('model'),
      untrustedMessages: j.optInt('untrusted_messages') ?? 0);
}

class WaConversation {
  const WaConversation(
      {required this.id,
      required this.waId,
      required this.lastMessageAt,
      required this.preview,
      required this.lastFromCustomer,
      required this.unread,
      required this.windowOpen,
      this.contactName,
      this.category,
      this.windowClosesAt,
      this.awaitingApproval = 0,
      this.summary,
      this.suggestion});
  final String id, waId, preview;
  final String? contactName, category, summary;
  final DateTime lastMessageAt;
  final DateTime? windowClosesAt;
  final bool lastFromCustomer, windowOpen;
  final int unread, awaitingApproval;
  final WaSuggestion? suggestion;

  /// The WhatsApp profile name, else the number.
  String get label {
    final name = contactName?.trim() ?? '';
    return name.isNotEmpty ? name : '+$waId';
  }

  /// Time left in the 24-hour customer-service window, or null when closed.
  Duration? windowLeft(DateTime now) {
    final closes = windowClosesAt;
    if (closes == null || !closes.isAfter(now)) return null;
    return closes.difference(now);
  }

  WaConversation read() => WaConversation(
      id: id,
      waId: waId,
      lastMessageAt: lastMessageAt,
      preview: preview,
      lastFromCustomer: lastFromCustomer,
      unread: 0,
      windowOpen: windowOpen,
      contactName: contactName,
      category: category,
      windowClosesAt: windowClosesAt,
      awaitingApproval: awaitingApproval,
      summary: summary,
      suggestion: suggestion);

  factory WaConversation.fromJson(JsonReader j) => WaConversation(
      id: j.string('id'),
      waId: j.string('wa_id'),
      contactName: j.optString('contact_name'),
      lastMessageAt: j.date('last_message_at').toLocal(),
      preview: j.optString('last_message_preview') ?? '',
      lastFromCustomer: j.optString('last_direction') != 'outbound',
      unread: j.optInt('unread_count') ?? 0,
      category: j.optString('category'),
      summary: j.optString('summary'),
      windowOpen: j.optBool('window_open') ?? false,
      windowClosesAt: j.optDate('window_closes_at')?.toLocal(),
      awaitingApproval: j.optInt('awaiting_approval') ?? 0,
      suggestion: j.optObject('suggestion') == null
          ? null
          : WaSuggestion.fromJson(j.object('suggestion')));
}

class WaMessage {
  const WaMessage(
      {required this.id,
      required this.inbound,
      required this.kind,
      required this.body,
      required this.status,
      required this.timestamp,
      this.error,
      this.templateName,
      this.hasMedia = false,
      this.approval});
  final String id, kind, body, status;
  final bool inbound, hasMedia;
  final String? error, templateName;
  final DateTime timestamp;

  /// The pending approval while the reply waits for one.
  final WorkspaceApproval? approval;

  factory WaMessage.fromJson(JsonReader j) => WaMessage(
      id: j.string('id'),
      inbound: j.string('direction') == 'inbound',
      kind: j.string('kind'),
      body: j.optString('body') ?? '',
      status: j.string('status'),
      error: j.optString('error'),
      timestamp: j.date('timestamp').toLocal(),
      templateName: j.optObject('template')?.optString('name'),
      hasMedia: j.optObject('media') != null,
      approval: j.optObject('approval') == null
          ? null
          : workspaceApprovalFromJson(j.object('approval')));
}

class WaThread {
  const WaThread(
      {required this.conversation,
      required this.messages,
      required this.hasMore});
  final WaConversation conversation;

  /// Oldest first.
  final List<WaMessage> messages;
  final bool hasMore;

  factory WaThread.fromJson(JsonReader j) => WaThread(
      conversation: WaConversation.fromJson(j.object('conversation')),
      messages: j.objects('messages', WaMessage.fromJson),
      hasMore: j.optBool('has_more') ?? false);
}

class WaSendResult {
  const WaSendResult(
      {required this.outcome, required this.message, required this.detail});
  final String outcome, detail;
  final WaMessage message;
  bool get awaitingApproval => outcome == 'awaiting_approval';

  factory WaSendResult.fromJson(JsonReader j) => WaSendResult(
      outcome: j.string('outcome'),
      message: WaMessage.fromJson(j.object('message')),
      detail: j.optString('detail') ?? '');
}

/// Messages oldest first with no duplicates (after paging back or polling).
List<WaMessage> mergeMessages(List<WaMessage> current, List<WaMessage> more) {
  final byId = {for (final m in current) m.id: m};
  for (final m in more) {
    byId[m.id] = m;
  }
  final merged = byId.values.toList()
    ..sort((a, b) {
      final time = a.timestamp.compareTo(b.timestamp);
      return time != 0 ? time : a.id.compareTo(b.id);
    });
  return merged;
}
