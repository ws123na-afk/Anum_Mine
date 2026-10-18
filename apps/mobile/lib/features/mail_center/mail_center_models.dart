import '../../data/json_read.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';

/// The Email Center (docs/email.md#email-center): the workspace's own mail
/// server read live over IMAP and written through approval over SMTP. These
/// models mirror `/api/v1/mail`; nothing here holds a password.

/// What the Email Center can do for the signed-in person.
class MailSetup {
  const MailSetup({
    required this.enabled,
    required this.configured,
    required this.tenantAllowed,
    required this.canAct,
    required this.allowedDomains,
    required this.maxRecipients,
    required this.sendPerHour,
    required this.accounts,
    this.attachmentMaxBytes = 10 * 1024 * 1024,
    this.attachmentsPerMessage = 10,
    this.attachmentsTotalBytes = 20 * 1024 * 1024,
    this.attachmentExtensions = const [],
    this.attachmentsAccepted = '',
    this.scheduleMaxDays = 30,
  });

  factory MailSetup.fromJson(JsonReader j) => MailSetup(
        enabled: j.boolean('enabled'),
        configured: j.optBool('configured') ?? false,
        tenantAllowed: j.optBool('tenant_allowed') ?? false,
        canAct: j.optBool('can_act') ?? false,
        allowedDomains: j.optStrings('allowed_domains') ?? const [],
        maxRecipients: j.optInt('max_recipients') ?? 50,
        sendPerHour: j.optInt('send_per_hour') ?? 30,
        accounts: j.optObjects('accounts', MailAccount.fromJson),
        attachmentMaxBytes:
            j.optInt('attachment_max_bytes') ?? 10 * 1024 * 1024,
        attachmentsPerMessage: j.optInt('attachments_per_message') ?? 10,
        attachmentsTotalBytes:
            j.optInt('attachments_total_bytes') ?? 20 * 1024 * 1024,
        attachmentExtensions: j.optStrings('attachment_extensions') ?? const [],
        attachmentsAccepted: j.optString('attachments_accepted') ?? '',
        scheduleMaxDays: j.optInt('schedule_max_days') ?? 30,
      );

  final bool enabled, configured, tenantAllowed, canAct;
  final List<String> allowedDomains;
  final int maxRecipients, sendPerHour;
  final List<MailAccount> accounts;

  /// Outgoing files: the largest file, files per message and their total.
  final int attachmentMaxBytes, attachmentsPerMessage, attachmentsTotalBytes;

  /// Accepted file types by extension, with the dot (`.pdf`).
  final List<String> attachmentExtensions;

  /// The API's sentence naming the accepted types.
  final String attachmentsAccepted;

  /// How far ahead a message may be scheduled.
  final int scheduleMaxDays;

  MailSetup withAccounts(List<MailAccount> list) => MailSetup(
        enabled: enabled,
        configured: configured,
        tenantAllowed: tenantAllowed,
        canAct: canAct,
        allowedDomains: allowedDomains,
        maxRecipients: maxRecipients,
        sendPerHour: sendPerHour,
        accounts: list,
        attachmentMaxBytes: attachmentMaxBytes,
        attachmentsPerMessage: attachmentsPerMessage,
        attachmentsTotalBytes: attachmentsTotalBytes,
        attachmentExtensions: attachmentExtensions,
        attachmentsAccepted: attachmentsAccepted,
        scheduleMaxDays: scheduleMaxDays,
      );

  /// Switched on, set up and allowed for this organisation.
  bool get available => enabled && configured && tenantAllowed;
}

class MailAccount {
  const MailAccount({
    required this.id,
    required this.address,
    required this.status,
    this.displayName,
    this.lastError,
    this.lastCheckedAt,
    this.access = 'owner',
    this.ownerUserId,
    this.sharedWith = 0,
    this.grantId,
  });

  factory MailAccount.fromJson(JsonReader j) => MailAccount(
        id: j.string('id'),
        address: j.string('address'),
        displayName: j.optString('display_name'),
        status: j.optString('status') ?? 'connected',
        lastError: j.optString('last_error'),
        lastCheckedAt: j.optDate('last_checked_at'),
        access: j.optString('access') ?? 'owner',
        ownerUserId: j.optString('owner_user_id'),
        sharedWith: j.optInt('shared_with') ?? 0,
        grantId: j.optString('grant_id'),
      );

  final String id, address, status;
  final String? displayName, lastError;
  final DateTime? lastCheckedAt;

  /// owner (the caller's own), send or read (shared with the caller).
  final String access;
  final String? ownerUserId;

  /// How many people the owner shared it with (0 for a shared mailbox).
  final int sharedWith;

  /// The caller's own share of a mailbox shared with them (to leave it).
  final String? grantId;

  bool get needsPassword => status == 'auth_failed';
  bool get healthy => status == 'connected';
  bool get isOwn => access == 'owner';

  /// Flags, moves, drafts and asking to send: the owner or a send share.
  bool get canWrite => access == 'owner' || access == 'send';
}

class MailFolder {
  const MailFolder({
    required this.id,
    required this.name,
    required this.total,
    required this.unread,
    this.role,
  });

  factory MailFolder.fromJson(JsonReader j) => MailFolder(
        id: j.string('id'),
        name: j.string('name'),
        role: j.optString('role'),
        total: j.optInt('total') ?? 0,
        unread: j.optInt('unread') ?? 0,
      );

  final String id, name;

  /// inbox, drafts, sent, archive, junk, trash, or null for other folders.
  final String? role;
  final int total, unread;
}

class MailPerson {
  const MailPerson(this.name, this.address);

  factory MailPerson.fromJson(JsonReader j) =>
      MailPerson(j.optString('name') ?? '', j.optString('address') ?? '');

  final String name, address;

  /// The name when there is one, else the address.
  String get label => name.trim().isEmpty ? address : name;
}

class MailMessage {
  const MailMessage({
    required this.uid,
    required this.folder,
    required this.subject,
    required this.date,
    required this.seen,
    required this.flagged,
    required this.answered,
    required this.maybeAttachments,
    this.sender,
    this.to = const [],
    this.cc = const [],
    this.messageId,
  });

  factory MailMessage.fromJson(JsonReader j) => MailMessage(
        uid: j.integer('uid'),
        folder: j.string('folder'),
        subject: j.optString('subject') ?? '',
        sender: j.optObject('sender') == null
            ? null
            : MailPerson.fromJson(j.object('sender')),
        to: j.optObjects('to', MailPerson.fromJson),
        cc: j.optObjects('cc', MailPerson.fromJson),
        date: j.optDate('date') ?? DateTime.fromMillisecondsSinceEpoch(0),
        seen: j.optBool('seen') ?? true,
        flagged: j.optBool('flagged') ?? false,
        answered: j.optBool('answered') ?? false,
        maybeAttachments: j.optBool('maybe_attachments') ?? false,
        messageId: j.optString('message_id'),
      );

  final int uid;
  final String folder, subject;
  final MailPerson? sender;
  final List<MailPerson> to, cc;
  final DateTime date;
  final bool seen, flagged, answered, maybeAttachments;

  /// The Message-ID header, which stored labels are matched against.
  final String? messageId;

  MailMessage copyWith({bool? seen, bool? flagged}) => MailMessage(
        uid: uid,
        folder: folder,
        subject: subject,
        sender: sender,
        to: to,
        cc: cc,
        date: date,
        seen: seen ?? this.seen,
        flagged: flagged ?? this.flagged,
        answered: answered,
        maybeAttachments: maybeAttachments,
        messageId: messageId,
      );
}

class MailPage {
  const MailPage(this.items, this.total, this.nextBefore);
  final List<MailMessage> items;
  final int total;
  final int? nextBefore;
}

class MailAttachment {
  const MailAttachment(this.filename, this.contentType, this.size,
      {this.index = 0, this.downloadable = true});

  factory MailAttachment.fromJson(JsonReader j) => MailAttachment(
        j.optString('filename') ?? '',
        j.optString('content_type') ?? 'application/octet-stream',
        j.optInt('size') ?? 0,
        index: j.optInt('index') ?? 0,
        downloadable: j.optBool('downloadable') ?? true,
      );

  final String filename, contentType;
  final int size, index;

  /// False when it is over the download limit: open it in webmail.
  final bool downloadable;

  /// A name to save it under.
  String get saveName =>
      filename.trim().isEmpty ? 'attachment-$index' : filename;
}

/// A file attached to a message written in ANUM (`OutgoingFile`).
class MailOutgoingFile {
  const MailOutgoingFile({
    required this.id,
    required this.filename,
    required this.contentType,
    required this.size,
    this.sha256 = '',
    this.scanned = false,
  });

  factory MailOutgoingFile.fromJson(JsonReader j) => MailOutgoingFile(
        id: j.optString('id') ?? '',
        filename: j.optString('filename') ?? '',
        contentType: j.optString('content_type') ?? 'application/octet-stream',
        size: j.optInt('size') ?? 0,
        sha256: j.optString('sha256') ?? '',
        scanned: j.optBool('scanned') ?? false,
      );

  final String id, filename, contentType, sha256;
  final int size;

  /// False when the server has no malware scanner installed.
  final bool scanned;
}

class MailDetail {
  const MailDetail({
    required this.message,
    required this.text,
    required this.textTruncated,
    required this.remoteImagesBlocked,
    required this.inlineImagesHidden,
    required this.attachments,
    required this.hasHtml,
    this.textFromHtml = false,
    this.replyTo = const [],
  });

  factory MailDetail.fromJson(JsonReader j) {
    final html = j.optString('html');
    final text = j.optString('text') ?? '';
    final fromHtml = text.trim().isEmpty && html != null;
    return MailDetail(
      message: MailMessage.fromJson(j.object('message')),
      replyTo: j.optObjects('reply_to', MailPerson.fromJson),
      // Plain text only: the app never renders the sender's HTML. When a
      // message has no text part, the server-sanitized HTML is reduced to
      // text here and shown as text.
      text: fromHtml ? htmlToText(html) : text,
      textFromHtml: fromHtml,
      textTruncated: j.optBool('text_truncated') ?? false,
      hasHtml: html != null,
      remoteImagesBlocked: j.optInt('remote_images_blocked') ?? 0,
      inlineImagesHidden: j.optInt('inline_images_hidden') ?? 0,
      attachments: j.optObjects('attachments', MailAttachment.fromJson),
    );
  }

  final MailMessage message;
  final List<MailPerson> replyTo;
  final String text;
  final bool textTruncated, hasHtml;

  /// The message had no text part: [text] was made from its sanitized HTML.
  final bool textFromHtml;
  final int remoteImagesBlocked, inlineImagesHidden;
  final List<MailAttachment> attachments;
}

/// Sanitized HTML to plain text for display (never interpreted as markup).
String htmlToText(String html) {
  var text = html
      .replaceAll(RegExp(r'<(br|BR)\s*/?>'), '\n')
      .replaceAll(
          RegExp(r'</(p|div|li|tr|h[1-6])>', caseSensitive: false), '\n')
      .replaceAll(RegExp(r'<[^>]*>'), '');
  const entities = {
    '&nbsp;': ' ',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
    '&#39;': "'",
    '&amp;': '&',
  };
  entities.forEach((from, to) => text = text.replaceAll(from, to));
  return text.replaceAll(RegExp(r'\n{3,}'), '\n\n').trim();
}

/// A message written in ANUM (new, reply, reply to all, forward) and where its
/// send stands. Sending always waits for an owner's approval.
class MailDraft {
  const MailDraft({
    required this.id,
    required this.accountId,
    required this.kind,
    required this.to,
    required this.cc,
    required this.bcc,
    required this.subject,
    required this.body,
    required this.status,
    this.error,
    this.approval,
    this.approvalNote,
    this.savedToSent,
    this.updatedAt,
    this.sendAt,
    this.attachments = const [],
    this.identityId,
    this.fromAddress,
    this.fromName,
    this.attachmentNote,
  });

  factory MailDraft.fromJson(JsonReader j) => MailDraft(
        id: j.string('id'),
        accountId: j.string('account_id'),
        kind: j.optString('kind') ?? 'new',
        to: j.optStrings('to') ?? const [],
        cc: j.optStrings('cc') ?? const [],
        bcc: j.optStrings('bcc') ?? const [],
        subject: j.optString('subject') ?? '',
        body: j.optString('body') ?? '',
        status: j.optString('status') ?? 'draft',
        error: j.optString('error'),
        approval: j.optObject('approval') == null
            ? null
            : workspaceApprovalFromJson(j.object('approval')),
        approvalNote: j.optString('approval_note'),
        savedToSent: j.optBool('saved_to_sent'),
        updatedAt: j.optDate('updated_at'),
        sendAt: j.optDate('send_at'),
        attachments: j.optObjects('attachments', MailOutgoingFile.fromJson),
        identityId: j.optString('identity_id'),
        fromAddress: j.optString('from_address'),
        fromName: j.optString('from_name'),
        attachmentNote: j.optString('attachment_note'),
      );

  final String id, accountId, kind, subject, body, status;
  final List<String> to, cc, bcc;
  final String? error, approvalNote;
  final WorkspaceApproval? approval;
  final bool? savedToSent;
  final DateTime? updatedAt;

  /// When a scheduled send goes (UTC); on a draft, the last time picked.
  final DateTime? sendAt;

  /// The files that go with the message.
  final List<MailOutgoingFile> attachments;

  /// The sender identity, or null for the mailbox address itself.
  final String? identityId;
  final String? fromAddress, fromName;

  /// A forward's files that were left out, and why.
  final String? attachmentNote;

  /// Still editable: a draft, a failed send, one waiting for approval or a
  /// scheduled one (editing withdraws the approval or the schedule).
  bool get editable =>
      status == 'draft' ||
      status == 'failed' ||
      status == 'pending_approval' ||
      status == 'scheduled';
  bool get waiting =>
      status == 'pending_approval' && approval?.status == 'pending';
  bool get scheduled => status == 'scheduled';

  /// The time it is scheduled for (approved or waiting), else null.
  DateTime? get scheduledFor =>
      status == 'scheduled' || status == 'pending_approval' ? sendAt : null;

  int get attachedBytes => attachments.fold(0, (sum, f) => sum + f.size);
}

/// The draft as edited on the phone, sent with `PATCH /drafts/{id}`.
class MailDraftEdit {
  const MailDraftEdit({
    required this.to,
    required this.cc,
    required this.bcc,
    required this.subject,
    required this.body,
    this.identityId,
    this.setIdentity = false,
  });

  final List<String> to, cc, bcc;
  final String subject, body;

  /// With [setIdentity], the sender identity to use (null: the mailbox
  /// address itself).
  final String? identityId;
  final bool setIdentity;

  Map<String, Object?> toJson() => {
        'to': to,
        'cc': cc,
        'bcc': bcc,
        'subject': subject,
        'body': body,
        if (setIdentity) 'identity_id': identityId,
      };

  /// Comma, semicolon or space separated addresses, trimmed, empties dropped.
  static List<String> split(String text) => [
        for (final part in text.split(RegExp(r'[,;\s]+')))
          if (part.trim().isNotEmpty) part.trim(),
      ];
}
