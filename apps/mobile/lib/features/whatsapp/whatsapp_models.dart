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

/// `GET /api/v1/whatsapp/account`. Never carries secrets: only hints that a
/// token is saved ([accessTokenHint], [appSecretSet], [verifyTokenSet]).
class WaAccount {
  const WaAccount(
      {required this.configured,
      required this.canManage,
      this.displayPhoneNumber,
      this.verifiedName,
      this.status,
      this.webhookPath = '/api/v1/whatsapp/webhook',
      this.deploymentProblems = const [],
      this.phoneNumberId,
      this.businessAccountId,
      this.qualityRating,
      this.accessTokenHint,
      this.appSecretSet = false,
      this.verifyTokenSet = false,
      this.ownerTestNumber,
      this.autoSendTemplates = const [],
      this.triageEnabled = true,
      this.messageRetentionDays,
      this.defaultRetentionDays = 0,
      this.lastError,
      this.lastCheckedAt,
      this.lastWebhookAt});
  final bool configured, canManage;
  final String? displayPhoneNumber, verifiedName, status;
  final String webhookPath;
  final List<String> deploymentProblems;
  final String? phoneNumberId, businessAccountId, qualityRating;
  final String? accessTokenHint, ownerTestNumber, lastError;
  final bool appSecretSet, verifyTokenSet, triageEnabled;
  final List<String> autoSendTemplates;

  /// The workspace's own retention choice (null: the deployment default; 0:
  /// keep until deleted) and that default.
  final int? messageRetentionDays;
  final int defaultRetentionDays;
  final DateTime? lastCheckedAt, lastWebhookAt;

  factory WaAccount.fromJson(JsonReader j) => WaAccount(
      configured: j.boolean('configured'),
      canManage: j.optBool('can_manage') ?? false,
      displayPhoneNumber: j.optString('display_phone_number'),
      verifiedName: j.optString('verified_name'),
      status: j.optString('status'),
      webhookPath: j.optString('webhook_path') ?? '/api/v1/whatsapp/webhook',
      deploymentProblems: j.optStrings('deployment_problems') ?? const [],
      phoneNumberId: j.optString('phone_number_id'),
      businessAccountId: j.optString('business_account_id'),
      qualityRating: j.optString('quality_rating'),
      accessTokenHint: j.optString('access_token_hint'),
      appSecretSet: j.optBool('app_secret_set') ?? false,
      verifyTokenSet: j.optBool('verify_token_set') ?? false,
      ownerTestNumber: j.optString('owner_test_number'),
      autoSendTemplates: j.optStrings('auto_send_templates') ?? const [],
      triageEnabled: j.optBool('triage_enabled') ?? true,
      messageRetentionDays: j.optInt('message_retention_days'),
      defaultRetentionDays: j.optInt('default_message_retention_days') ?? 0,
      lastError: j.optString('last_error'),
      lastCheckedAt: j.optDate('last_checked_at')?.toLocal(),
      lastWebhookAt: j.optDate('last_webhook_at')?.toLocal());
}

/// One Meta message template (`GET /api/v1/whatsapp/templates`).
class WaTemplate {
  const WaTemplate(
      {required this.name,
      required this.language,
      required this.status,
      required this.category,
      required this.body,
      required this.parameterCount,
      this.header,
      this.footer,
      this.buttons = const [],
      this.autoSend = false});
  final String name, language, status, category, body;
  final int parameterCount;
  final String? header, footer;
  final List<String> buttons;
  final bool autoSend;

  bool get approved => status == 'APPROVED';
  String get key => '$name|$language';

  WaTemplate withAutoSend(bool value) => WaTemplate(
      name: name,
      language: language,
      status: status,
      category: category,
      body: body,
      parameterCount: parameterCount,
      header: header,
      footer: footer,
      buttons: buttons,
      autoSend: value);

  factory WaTemplate.fromJson(JsonReader j) => WaTemplate(
      name: j.string('name'),
      language: j.string('language'),
      status: j.string('status'),
      category: j.string('category'),
      body: j.optString('body') ?? '',
      parameterCount: j.optInt('parameter_count') ?? 0,
      header: j.optString('header'),
      footer: j.optString('footer'),
      buttons: j.optStrings('buttons') ?? const [],
      autoSend: j.optBool('auto_send') ?? false);
}

class WaTemplateList {
  const WaTemplateList({required this.templates, required this.fetchedAt});
  final List<WaTemplate> templates;
  final DateTime fetchedAt;

  factory WaTemplateList.fromJson(JsonReader j) => WaTemplateList(
      templates: j.objects('templates', WaTemplate.fromJson),
      fetchedAt: j.date('fetched_at').toLocal());
}

final _placeholder = RegExp(r'\{\{\s*([0-9]+|[a-z_][a-z0-9_]*)\s*\}\}');

/// The template as the customer will read it; unfilled placeholders stay
/// visible (the same rule as the API and the web app).
String fillTemplate(String body, List<String> parameters) {
  final order = <String, int>{};
  return body.replaceAllMapped(_placeholder, (match) {
    final key = match.group(1)!;
    final number = int.tryParse(key);
    final index = number != null
        ? number - 1
        : order.putIfAbsent(key, () => order.length);
    final value =
        index >= 0 && index < parameters.length ? parameters[index].trim() : '';
    return value.isEmpty ? match.group(0)! : value;
  });
}

/// The body for `PUT /api/v1/whatsapp/account`. Secrets are sent only when
/// typed, once, and omitted secrets keep the saved ones on the server. Call
/// [clearSecrets] as soon as the request ends.
class WaAccountUpdate {
  WaAccountUpdate(
      {required this.phoneNumberId,
      required this.businessAccountId,
      this.ownerTestNumber = '',
      this.triageEnabled = true,
      this.autoSendTemplates = const [],
      this.retentionDays,
      this.sendRetention = false,
      this.accessToken = '',
      this.appSecret = '',
      this.verifyToken = ''});

  /// Everything the saved account holds, so changing one setting (the
  /// auto-send list) leaves the others as they were.
  factory WaAccountUpdate.of(WaAccount account,
          {List<String>? autoSendTemplates}) =>
      WaAccountUpdate(
          phoneNumberId: account.phoneNumberId ?? '',
          businessAccountId: account.businessAccountId ?? '',
          ownerTestNumber: account.ownerTestNumber ?? '',
          triageEnabled: account.triageEnabled,
          autoSendTemplates: autoSendTemplates ?? account.autoSendTemplates,
          retentionDays: account.messageRetentionDays,
          sendRetention: true);

  String phoneNumberId, businessAccountId, ownerTestNumber;
  bool triageEnabled;
  List<String> autoSendTemplates;

  /// With [sendRetention]: null follows the deployment default, 0 keeps
  /// messages until deleted. Without it the saved choice stays.
  int? retentionDays;
  bool sendRetention;
  String accessToken, appSecret, verifyToken;

  Map<String, Object?> body() {
    final phone = ownerTestNumber.replaceAll(RegExp(r'[\s+()\-]'), '');
    return {
      'phone_number_id': phoneNumberId.trim(),
      'business_account_id': businessAccountId.trim(),
      'owner_test_number': phone.isEmpty ? null : phone,
      'triage_enabled': triageEnabled,
      'auto_send_templates': {...autoSendTemplates}.toList()..sort(),
      if (sendRetention) 'message_retention_days': retentionDays,
      if (accessToken.trim().isNotEmpty) 'access_token': accessToken.trim(),
      if (appSecret.trim().isNotEmpty) 'app_secret': appSecret.trim(),
      if (verifyToken.trim().isNotEmpty) 'verify_token': verifyToken.trim(),
    };
  }

  void clearSecrets() {
    accessToken = '';
    appSecret = '';
    verifyToken = '';
  }

  @override
  String toString() => 'WaAccountUpdate(secrets hidden)';
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

/// A message's attachment (`media` on a message). Its bytes come from
/// `GET /api/v1/whatsapp/messages/{id}/media`.
class WaMedia {
  const WaMedia(
      {this.mimeType, this.filename, this.caption, this.voice = false});
  final String? mimeType, filename, caption;
  final bool voice;

  /// Shown inline in the viewer (the API serves these types inline).
  bool get isImage => RegExp(r'^image/(jpeg|png|webp|gif)$')
      .hasMatch((mimeType ?? '').toLowerCase());

  factory WaMedia.fromJson(JsonReader j) => WaMedia(
      mimeType: j.optString('mime_type'),
      filename: j.optString('filename'),
      caption: j.optString('caption'),
      voice: j.optBool('voice') ?? false);
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
      this.media,
      this.approval});
  final String id, kind, body, status;
  final bool inbound;
  final String? error, templateName;

  /// The attachment, when the message has one (downloaded on demand).
  final WaMedia? media;
  bool get hasMedia => media != null;
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
      media: j.optObject('media') == null
          ? null
          : WaMedia.fromJson(j.object('media')),
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
