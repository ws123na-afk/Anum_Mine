import 'package:flutter/foundation.dart' show listEquals;

import '../../data/json_read.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';

/// One connected mailbox (docs/email.md). Never carries a token.
class EmailAccount {
  const EmailAccount({
    required this.id,
    required this.provider,
    required this.providerName,
    required this.address,
    required this.status,
    required this.visibility,
    this.lastSyncAt,
    this.lastError,
    this.unreadCount = 0,
    this.messageCount = 0,
  });

  final String id, provider, providerName, address, status, visibility;
  final DateTime? lastSyncAt;
  final String? lastError;
  final int unreadCount, messageCount;

  bool get needsReconnect => status == 'reauth_required';

  factory EmailAccount.fromJson(JsonReader j) => EmailAccount(
        id: j.string('id'),
        provider: j.string('provider'),
        providerName: j.optString('provider_name') ?? j.string('provider'),
        address: j.string('address'),
        status: j.optString('status') ?? 'connected',
        visibility: j.optString('visibility') ?? 'workspace',
        lastSyncAt: j.optDate('last_sync_at'),
        lastError: j.optString('last_error'),
        unreadCount: j.optInt('unread_count') ?? 0,
        messageCount: j.optInt('message_count') ?? 0,
      );
}

/// A message's metadata as the inbox lists it. The text is fetched only when
/// the message is opened ([EmailDetail]).
class EmailMessage {
  const EmailMessage({
    required this.id,
    required this.accountId,
    required this.subject,
    required this.fromAddress,
    required this.receivedAt,
    this.fromName,
    this.snippet = '',
    this.summary,
    this.category,
    this.priority,
    this.isRead = false,
    this.hasAttachments = false,
    this.to = const [],
  });

  final String id, accountId, subject, fromAddress, snippet;
  final String? fromName, summary, category, priority;
  final DateTime receivedAt;
  final bool isRead, hasAttachments;
  final List<String> to;

  String get sender =>
      (fromName?.trim().isNotEmpty ?? false) ? fromName!.trim() : fromAddress;

  EmailMessage copyWith({bool? isRead}) => EmailMessage(
        id: id,
        accountId: accountId,
        subject: subject,
        fromAddress: fromAddress,
        receivedAt: receivedAt,
        fromName: fromName,
        snippet: snippet,
        summary: summary,
        category: category,
        priority: priority,
        isRead: isRead ?? this.isRead,
        hasAttachments: hasAttachments,
        to: to,
      );

  factory EmailMessage.fromJson(JsonReader j) => EmailMessage(
        id: j.string('id'),
        accountId: j.string('account_id'),
        subject: j.optString('subject') ?? '',
        fromAddress: j.optString('from_address') ?? '',
        fromName: j.optString('from_name'),
        receivedAt: j.date('received_at'),
        snippet: j.optString('snippet') ?? '',
        summary: j.optString('summary'),
        category: j.optString('category'),
        priority: j.optString('priority'),
        isRead: j.optBool('is_read') ?? false,
        hasAttachments: j.optBool('has_attachments') ?? false,
        to: [
          for (final value in j.optList('to') ?? const [])
            if (value is String) value,
        ],
      );
}

List<String> _strings(JsonReader j, String key) => [
      for (final value in j.optList(key) ?? const [])
        if (value is String) value,
    ];

/// A draft (a reply to a message, or a new email), with who wrote it
/// (provenance) and its send approval.
class EmailDraft {
  const EmailDraft({
    required this.id,
    required this.to,
    required this.subject,
    required this.body,
    required this.status,
    required this.source,
    this.cc = const [],
    this.bcc = const [],
    this.kind = 'reply',
    this.accountId,
    this.messageId,
    this.modelName,
    this.approval,
    this.approvalNote,
    this.error,
  });

  final String id, subject, body, status, source;
  final List<String> to, cc, bcc;

  /// `new` (composed from scratch, no [messageId]) or `reply`.
  final String kind;
  final String? accountId, messageId;
  final String? modelName, approvalNote, error;

  /// The pending send approval, when one is waiting (approve with its hash).
  final WorkspaceApproval? approval;

  bool get byModel => source == 'model';
  bool get sent => status == 'sent';
  bool get isNew => kind == 'new';

  EmailDraft copyWith({String? status, WorkspaceApproval? approval}) =>
      EmailDraft(
        id: id,
        to: to,
        subject: subject,
        body: body,
        status: status ?? this.status,
        source: source,
        cc: cc,
        bcc: bcc,
        kind: kind,
        accountId: accountId,
        messageId: messageId,
        modelName: modelName,
        approval: approval ?? this.approval,
        approvalNote: approvalNote,
        error: error,
      );

  factory EmailDraft.fromJson(JsonReader j) => EmailDraft(
        id: j.string('id'),
        to: _strings(j, 'to'),
        cc: _strings(j, 'cc'),
        bcc: _strings(j, 'bcc'),
        kind: j.optString('kind') ?? 'reply',
        accountId: j.optString('account_id'),
        messageId: j.optString('message_id'),
        subject: j.optString('subject') ?? '',
        body: j.optString('body') ?? '',
        status: j.optString('status') ?? 'draft',
        source: j.optString('source') ?? 'person',
        modelName: j.optString('model_name'),
        approval: j.optObject('approval') == null
            ? null
            : workspaceApprovalFromJson(j.object('approval')),
        approvalNote: j.optString('approval_note'),
        error: j.optString('error'),
      );
}

/// What a new email says, as `POST /email/drafts` and `PATCH
/// /email/drafts/{id}` take it (docs/email.md). The API caps each address
/// list at [maxAddresses], the subject at [maxSubject] and the body at
/// [maxBody] characters.
class EmailDraftFields {
  const EmailDraftFields({
    required this.accountId,
    this.to = const [],
    this.cc = const [],
    this.bcc = const [],
    this.subject = '',
    this.body = '',
  });

  static const maxAddresses = 50, maxSubject = 500, maxBody = 20000;

  final String accountId, subject, body;
  final List<String> to, cc, bcc;

  /// The PATCH body: everything but the mailbox, which a draft keeps.
  Map<String, Object?> toPatch() => {
        'to': to,
        'cc': cc,
        'bcc': bcc,
        'subject': subject,
        'body': body,
      };

  Map<String, Object?> toCreate() => {'account_id': accountId, ...toPatch()};

  @override
  bool operator ==(Object other) =>
      other is EmailDraftFields &&
      other.accountId == accountId &&
      other.subject == subject &&
      other.body == body &&
      listEquals(other.to, to) &&
      listEquals(other.cc, cc) &&
      listEquals(other.bcc, bcc);

  @override
  int get hashCode => Object.hash(accountId, subject, body, Object.hashAll(to),
      Object.hashAll(cc), Object.hashAll(bcc));
}

final _address = RegExp(r'^[^\s@<>,;"]+@[^\s@<>,;"]+\.[^\s@<>,;".]+$');

/// Splits a To, Cc or Bcc field on commas and semicolons: the addresses and
/// the entries that are not a plain e-mail address.
({List<String> valid, List<String> invalid}) splitAddresses(String text) {
  final valid = <String>[], invalid = <String>[];
  for (final part in text.split(RegExp('[,;]'))) {
    final value = part.trim();
    if (value.isEmpty) continue;
    (_address.hasMatch(value) ? valid : invalid).add(value);
  }
  return (valid: valid, invalid: invalid);
}

/// An opened message: its text (plain, capped by the API), the drafts.
class EmailDetail {
  const EmailDetail({
    required this.message,
    this.body,
    this.bodyError,
    this.truncated = false,
    this.attachments = const [],
    this.drafts = const [],
  });

  final EmailMessage message;
  final String? body, bodyError;
  final bool truncated;
  final List<String> attachments;
  final List<EmailDraft> drafts;

  factory EmailDetail.fromJson(JsonReader j) => EmailDetail(
        message: EmailMessage.fromJson(j.object('message')),
        body: j.optString('body'),
        bodyError: j.optString('body_error'),
        truncated: j.optBool('body_truncated') ?? false,
        attachments: j.optObjects('attachments', (a) => a.string('filename')),
        drafts: j.optObjects('drafts', EmailDraft.fromJson),
      );
}

class EmailPage {
  const EmailPage(this.items, this.nextCursor);
  final List<EmailMessage> items;
  final String? nextCursor;
}
