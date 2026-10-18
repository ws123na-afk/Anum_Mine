import '../../data/json_read.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';
import 'mail_center_models.dart';

/// Shares, sender identities, contacts and AI results of the Email Center
/// (docs/email.md#email-center). These mirror `/api/v1/mail` and
/// `/api/v1/mail/ai`; none of them holds a password or mail text beyond what
/// the API returns for display.

/// One share of a mailbox the caller owns.
class MailGrant {
  const MailGrant({
    required this.id,
    required this.accountId,
    required this.granteeUserId,
    required this.access,
    this.granteeEmail,
  });

  factory MailGrant.fromJson(JsonReader j) => MailGrant(
        id: j.string('id'),
        accountId: j.optString('account_id') ?? '',
        granteeUserId: j.optString('grantee_user_id') ?? '',
        granteeEmail: j.optString('grantee_email'),
        access: j.optString('access') ?? 'read',
      );

  final String id, accountId, granteeUserId, access;
  final String? granteeEmail;

  String get label => granteeEmail ?? granteeUserId;
}

/// An active owner or member of the workspace the mailbox can be shared with.
class MailShareCandidate {
  const MailShareCandidate({
    required this.userId,
    required this.role,
    this.email,
    this.grantId,
    this.access,
  });

  factory MailShareCandidate.fromJson(JsonReader j) => MailShareCandidate(
        userId: j.string('user_id'),
        email: j.optString('email'),
        role: j.optString('role') ?? 'member',
        grantId: j.optString('grant_id'),
        access: j.optString('access'),
      );

  final String userId, role;
  final String? email, grantId, access;

  String get label => email ?? userId;
}

/// An address a mailbox sends as, with its name and plain text signature.
class MailIdentity {
  const MailIdentity({
    required this.id,
    required this.accountId,
    required this.address,
    required this.signature,
    required this.isDefault,
    this.displayName,
  });

  factory MailIdentity.fromJson(JsonReader j) => MailIdentity(
        id: j.string('id'),
        accountId: j.optString('account_id') ?? '',
        address: j.string('address'),
        displayName: j.optString('display_name'),
        signature: j.optString('signature') ?? '',
        isDefault: j.optBool('is_default') ?? false,
      );

  final String id, accountId, address, signature;
  final String? displayName;
  final bool isDefault;

  String get label =>
      (displayName ?? '').trim().isEmpty ? address : '$displayName <$address>';
}

/// What an identity is saved as; the address is set only when it is added.
class MailIdentityEdit {
  const MailIdentityEdit({
    this.address = '',
    this.displayName = '',
    this.signature = '',
    this.isDefault = false,
  });

  final String address, displayName, signature;
  final bool isDefault;

  Map<String, Object?> toJson({required bool create}) => {
        if (create) 'address': address.trim(),
        'display_name': displayName.trim().isEmpty ? null : displayName.trim(),
        'signature': signature,
        'is_default': isDefault,
      };
}

/// One entry of the person's own address book.
class MailContact {
  const MailContact({
    required this.id,
    required this.email,
    this.name = '',
    this.company = '',
    this.phone = '',
    this.notes = '',
  });

  factory MailContact.fromJson(JsonReader j) => MailContact(
        id: j.string('id'),
        email: j.string('email'),
        name: j.optString('name') ?? '',
        company: j.optString('company') ?? '',
        phone: j.optString('phone') ?? '',
        notes: j.optString('notes') ?? '',
      );

  final String id, email, name, company, phone, notes;

  String get label => name.trim().isEmpty ? email : name;
}

class MailContactPage {
  const MailContactPage(this.items, this.total, this.nextCursor);
  final List<MailContact> items;
  final int total;
  final String? nextCursor;
}

/// What a contact is saved as (`POST` and `PATCH /contacts`).
class MailContactEdit {
  const MailContactEdit({
    required this.email,
    this.name = '',
    this.company = '',
    this.phone = '',
    this.notes = '',
  });

  final String email, name, company, phone, notes;

  Map<String, Object?> toJson() => {
        'email': email.trim(),
        'name': name.trim(),
        'company': company.trim(),
        'phone': phone.trim(),
        'notes': notes.trim(),
      };
}

/// `GET /mail/ai/status`: whether a real model is set up for the workspace.
class MailAiStatus {
  const MailAiStatus(
      {required this.available,
      required this.canRead,
      required this.canAct,
      this.reason});

  factory MailAiStatus.fromJson(JsonReader j) => MailAiStatus(
        available: j.optBool('available') ?? false,
        reason: j.optString('reason'),
        canRead: j.optBool('can_read') ?? false,
        canAct: j.optBool('can_act') ?? false,
      );

  final bool available, canRead, canAct;
  final String? reason;
}

/// A summary of one message or its thread.
class MailSummary {
  const MailSummary({
    required this.summary,
    required this.keyPoints,
    required this.actionItems,
    required this.messages,
    required this.thread,
    required this.injectionSuspected,
  });

  factory MailSummary.fromJson(JsonReader j) => MailSummary(
        summary: j.optString('summary') ?? '',
        keyPoints: j.optStrings('key_points') ?? const [],
        actionItems: j.optStrings('action_items') ?? const [],
        messages: j.optInt('messages') ?? 1,
        thread: j.optBool('thread') ?? false,
        injectionSuspected: j.optBool('injection_suspected') ?? false,
      );

  final String summary;
  final List<String> keyPoints, actionItems;
  final int messages;
  final bool thread, injectionSuspected;
}

/// A label: category (customer, order, invoice, ...) and priority.
class MailLabel {
  const MailLabel({
    required this.uid,
    required this.category,
    required this.priority,
    this.reason = '',
    this.source = 'model',
    this.injectionSuspected = false,
  });

  factory MailLabel.fromJson(JsonReader j, {int? uid}) => MailLabel(
        uid: uid ?? j.optInt('uid') ?? 0,
        category: j.optString('category') ?? 'other',
        priority: j.optString('priority') ?? 'normal',
        reason: j.optString('reason') ?? '',
        source: j.optString('source') ?? 'model',
        injectionSuspected: j.optBool('injection_suspected') ?? false,
      );

  final int uid;
  final String category, priority, reason, source;
  final bool injectionSuspected;
}

/// `POST /mail/ai/classify-page`: labels for the shown page.
class MailLabelPage {
  const MailLabelPage(
      this.labels, this.labelled, this.skipped, this.failed, this.summary);

  factory MailLabelPage.fromJson(JsonReader j) => MailLabelPage(
        j.optObjects('labels', MailLabel.fromJson),
        j.optInt('labelled') ?? 0,
        j.optInt('skipped') ?? 0,
        j.optInt('failed') ?? 0,
        j.optString('summary') ?? '',
      );

  final List<MailLabel> labels;
  final int labelled, skipped, failed;
  final String summary;
}

/// `POST /mail/ai/draft-reply`: the draft written, or the approval it waits
/// for when the workspace asks approval for medium risk.
class MailDraftReply {
  const MailDraftReply(
      {required this.status,
      this.draft,
      this.approval,
      this.injectionSuspected = false});

  factory MailDraftReply.fromJson(JsonReader j) => MailDraftReply(
        status: j.optString('status') ?? 'done',
        draft: j.optObject('draft') == null
            ? null
            : MailDraft.fromJson(j.object('draft')),
        approval: j.optObject('approval') == null
            ? null
            : workspaceApprovalFromJson(j.object('approval')),
        injectionSuspected: j.optBool('injection_suspected') ?? false,
      );

  final String status;
  final MailDraft? draft;
  final WorkspaceApproval? approval;
  final bool injectionSuspected;
}

/// A picked device file, before it is attached.
class PickedMailFile {
  const PickedMailFile(this.path, this.name, this.size);
  final String path, name;
  final int size;
}

/// A file the server would refuse, said before the upload starts.
class MailFileProblem {
  const MailFileProblem(this.key, this.name);

  /// type, empty, size, count or total.
  final String key;
  final String name;
}

/// Splits [files] into those to upload and those the limits refuse (the API
/// checks again, with the malware scanner).
(List<PickedMailFile>, List<MailFileProblem>) fileProblemsFor(
    List<PickedMailFile> files, MailDraft draft, MailSetup setup) {
  var count = draft.attachments.length;
  var total = draft.attachedBytes;
  final ok = <PickedMailFile>[];
  final problems = <MailFileProblem>[];
  final extensions = setup.attachmentExtensions;
  for (final file in files) {
    final dot = file.name.lastIndexOf('.');
    final ext = dot > 0 ? file.name.substring(dot).toLowerCase() : '';
    final String? key;
    if (extensions.isNotEmpty && !extensions.contains(ext)) {
      key = 'type';
    } else if (file.size == 0) {
      key = 'empty';
    } else if (file.size > setup.attachmentMaxBytes) {
      key = 'size';
    } else if (count + 1 > setup.attachmentsPerMessage) {
      key = 'count';
    } else if (total + file.size > setup.attachmentsTotalBytes) {
      key = 'total';
    } else {
      key = null;
    }
    if (key == null) {
      ok.add(file);
      count += 1;
      total += file.size;
    } else {
      problems.add(MailFileProblem(key, file.name));
    }
  }
  return (ok, problems);
}
