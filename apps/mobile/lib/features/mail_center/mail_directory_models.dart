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

/// A mailbox's last background check.
class MailSyncState {
  const MailSyncState(
      {required this.status,
      this.inboxUnread,
      this.checkedAt,
      this.nextSyncAt,
      this.lastError});

  factory MailSyncState.fromJson(JsonReader j) => MailSyncState(
        status: j.optString('status') ?? 'pending',
        inboxUnread: j.optInt('inbox_unread'),
        checkedAt: j.optDate('checked_at'),
        nextSyncAt: j.optDate('next_sync_at'),
        lastError: j.optString('last_error'),
      );

  /// pending, ok, auth_failed, offline or error.
  final String status;
  final int? inboxUnread;
  final DateTime? checkedAt, nextSyncAt;
  final String? lastError;
}

/// Retention and background sync of one mailbox (`SettingsView`). A change
/// waits for the `change_mail_settings` approval of exactly that change.
class MailSettings {
  const MailSettings({
    required this.accountId,
    required this.access,
    required this.canChange,
    required this.backgroundSync,
    required this.version,
    this.sentRetentionDays,
    this.draftRetentionDays,
    this.labelRetentionDays,
    this.retentionChoices = const [7, 30, 90, 180, 365, 730],
    this.maxRetentionDays = 3650,
    this.syncRunning = true,
    this.syncIntervalSeconds = 300,
    this.sync,
    this.approval,
    this.pending,
  });

  factory MailSettings.fromJson(JsonReader j) {
    final pending = j.optObject('pending');
    return MailSettings(
      accountId: j.string('account_id'),
      access: j.optString('access') ?? 'owner',
      canChange: j.optBool('can_change') ?? false,
      sentRetentionDays: j.optInt('sent_retention_days'),
      draftRetentionDays: j.optInt('draft_retention_days'),
      labelRetentionDays: j.optInt('label_retention_days'),
      backgroundSync: j.optBool('background_sync') ?? true,
      version: j.optInt('version') ?? 0,
      retentionChoices: [
        for (final v in (j['retention_choices'] as List? ?? const []))
          if (v is int) v
      ],
      maxRetentionDays: j.optInt('max_retention_days') ?? 3650,
      syncRunning: j.optBool('sync_running') ?? true,
      syncIntervalSeconds: j.optInt('sync_interval_seconds') ?? 300,
      sync: j.optObject('sync') == null
          ? null
          : MailSyncState.fromJson(j.object('sync')),
      approval: j.optObject('approval') == null
          ? null
          : workspaceApprovalFromJson(j.object('approval')),
      pending: pending == null ? null : MailSettingsEdit.fromJson(pending),
    );
  }

  final String accountId, access;
  final bool canChange, backgroundSync, syncRunning;
  final int version, maxRetentionDays, syncIntervalSeconds;
  final int? sentRetentionDays, draftRetentionDays, labelRetentionDays;
  final List<int> retentionChoices;
  final MailSyncState? sync;

  /// The change waiting for approval, and that approval.
  final WorkspaceApproval? approval;
  final MailSettingsEdit? pending;

  MailSettingsEdit get values => MailSettingsEdit(
        sentRetentionDays: sentRetentionDays,
        draftRetentionDays: draftRetentionDays,
        labelRetentionDays: labelRetentionDays,
        backgroundSync: backgroundSync,
      );
}

class MailSettingsEdit {
  const MailSettingsEdit({
    this.sentRetentionDays,
    this.draftRetentionDays,
    this.labelRetentionDays,
    this.backgroundSync = true,
  });

  factory MailSettingsEdit.fromJson(JsonReader j) => MailSettingsEdit(
        sentRetentionDays: j.optInt('sent_retention_days'),
        draftRetentionDays: j.optInt('draft_retention_days'),
        labelRetentionDays: j.optInt('label_retention_days'),
        backgroundSync: j.optBool('background_sync') ?? true,
      );

  /// Days, or null to keep while the mailbox is connected.
  final int? sentRetentionDays, draftRetentionDays, labelRetentionDays;
  final bool backgroundSync;

  MailSettingsEdit copyWith(String field, int? days) => MailSettingsEdit(
        sentRetentionDays: field == 'sent' ? days : sentRetentionDays,
        draftRetentionDays: field == 'draft' ? days : draftRetentionDays,
        labelRetentionDays: field == 'label' ? days : labelRetentionDays,
        backgroundSync: backgroundSync,
      );

  MailSettingsEdit withSync(bool value) => MailSettingsEdit(
        sentRetentionDays: sentRetentionDays,
        draftRetentionDays: draftRetentionDays,
        labelRetentionDays: labelRetentionDays,
        backgroundSync: value,
      );

  int? days(String field) => switch (field) {
        'sent' => sentRetentionDays,
        'draft' => draftRetentionDays,
        _ => labelRetentionDays,
      };

  bool sameAs(MailSettingsEdit other) =>
      sentRetentionDays == other.sentRetentionDays &&
      draftRetentionDays == other.draftRetentionDays &&
      labelRetentionDays == other.labelRetentionDays &&
      backgroundSync == other.backgroundSync;

  Map<String, Object?> toJson(int baseVersion) => {
        'sent_retention_days': sentRetentionDays,
        'draft_retention_days': draftRetentionDays,
        'label_retention_days': labelRetentionDays,
        'background_sync': backgroundSync,
        'base_version': baseVersion,
      };
}

/// A forwarding rule: forwards new inbox mail that matches, only while an
/// owner has approved exactly what it does (`activate_mail_forward_rule`).
class MailRule {
  const MailRule({
    required this.id,
    required this.name,
    required this.status,
    required this.forwardTo,
    this.fromContains = '',
    this.subjectContains = '',
    this.approval,
    this.approvalNote,
    this.lastRunAt,
    this.lastError,
    this.forwardedCount = 0,
  });

  factory MailRule.fromJson(JsonReader j) => MailRule(
        id: j.string('id'),
        name: j.optString('name') ?? '',
        status: j.optString('status') ?? 'pending_approval',
        forwardTo: j.optStrings('forward_to') ?? const [],
        fromContains: j.optString('from_contains') ?? '',
        subjectContains: j.optString('subject_contains') ?? '',
        approval: j.optObject('approval') == null
            ? null
            : workspaceApprovalFromJson(j.object('approval')),
        approvalNote: j.optString('approval_note'),
        lastRunAt: j.optDate('last_run_at'),
        lastError: j.optString('last_error'),
        forwardedCount: j.optInt('forwarded_count') ?? 0,
      );

  final String id, name, status, fromContains, subjectContains;
  final List<String> forwardTo;
  final WorkspaceApproval? approval;
  final String? approvalNote, lastError;
  final DateTime? lastRunAt;
  final int forwardedCount;
}

class MailRuleEdit {
  const MailRuleEdit(
      {required this.name,
      required this.forwardTo,
      this.fromContains = '',
      this.subjectContains = ''});

  final String name, fromContains, subjectContains;
  final List<String> forwardTo;

  Map<String, Object?> toJson() => {
        'name': name.trim(),
        'from_contains': fromContains.trim(),
        'subject_contains': subjectContains.trim(),
        'forward_to': forwardTo,
      };
}
