import '../../data/api_models.dart';
import '../../data/api_client.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';
import 'mail_center_models.dart';
import 'mail_directory_models.dart';

/// `/api/v1/mail` (docs/email.md#email-center). A mailbox is private to the
/// person who connected it; sending and deleting for good wait for an owner's
/// approval of the exact call (payload hash).
abstract interface class MailCenterRepository {
  Future<MailSetup> setup();
  Future<MailAccount> connect(String address, String password,
      {String? displayName});
  Future<MailAccount> updatePassword(String accountId, String password);
  Future<MailAccount> check(String accountId);
  Future<void> disconnect(String accountId);
  Future<List<MailFolder>> folders(String accountId);
  Future<MailPage> messages(
    String accountId, {
    required String folder,
    int? before,
    String? query,
    bool unread = false,
    bool flagged = false,
  });
  Future<MailDetail> read(String accountId, String folder, int uid);
  Future<void> setFlag(String accountId, String folder, int uid,
      {required String flag, required bool value});

  /// [to] is a role (inbox, archive, junk, trash) or a folder id.
  Future<void> move(String accountId, String folder, int uid, String to);

  /// Read or unread, starred or not, for up to 200 messages at once.
  Future<void> setFlags(String accountId, String folder, List<int> uids,
      {required String flag, required bool value});

  /// Moves up to 200 messages to a role or folder id.
  Future<void> moveMany(
      String accountId, String folder, List<int> uids, String to);

  /// Delete for good (Trash and Junk only): the approval of exactly [uids].
  Future<WorkspaceApproval?> deleteForGood(
      String accountId, String folder, List<int> uids);

  Future<List<MailDraft>> drafts(String accountId);

  /// Messages with a send time, scheduled or waiting for approval.
  Future<List<MailDraft>> scheduled(String accountId);
  Future<MailDraft> getDraft(String draftId);
  Future<MailDraft> createDraft(String accountId,
      {String kind = 'new', String? folder, int? uid});
  Future<MailDraft> updateDraft(String draftId, MailDraftEdit edit);
  Future<void> deleteDraft(String draftId);

  /// Asks to send with [key] (one key per attempt; a retry reuses it), now
  /// or at [sendAt].
  Future<MailDraft> askToSend(String draftId, String key, {DateTime? sendAt});

  /// Back to a draft (409 when it is already being sent).
  Future<MailDraft> cancelSchedule(String draftId);
  Future<void> removeAttachment(String draftId, String fileId);

  /// Approves with the displayed payload hash; the run's result sentence.
  Future<String?> approve(WorkspaceApproval approval);

  // -- sharing, identities, contacts ------------------------------------------
  Future<List<MailIdentity>> identities(String accountId);
  Future<MailIdentity> createIdentity(String accountId, MailIdentityEdit edit);
  Future<MailIdentity> updateIdentity(String identityId, MailIdentityEdit edit);
  Future<void> deleteIdentity(String identityId);
  Future<List<MailGrant>> grants(String accountId);
  Future<List<MailShareCandidate>> shareCandidates(String accountId,
      {String? query});
  Future<MailGrant> share(String accountId, String userId, String access);
  Future<MailGrant> updateGrant(String grantId, String access);

  /// The owner stops sharing, or the person it was shared with leaves.
  Future<void> removeGrant(String grantId);
  Future<MailContactPage> contacts({String? query, String? cursor, int limit});
  Future<MailContact> createContact(MailContactEdit edit);
  Future<MailContact> updateContact(String contactId, MailContactEdit edit);
  Future<void> deleteContact(String contactId);

  // -- retention, sync and forwarding rules ------------------------------------
  Future<MailSettings> settings(String accountId);

  /// Asks to change the settings from [baseVersion]: the approval of exactly
  /// that change (409 when someone changed them first).
  Future<WorkspaceApproval> changeSettings(
      String accountId, MailSettingsEdit edit, int baseVersion);
  Future<void> withdrawSettings(String accountId);
  Future<List<MailRule>> rules(String accountId);

  /// Create, edit and resume each ask for the rule's approval again.
  Future<MailRule> createRule(String accountId, MailRuleEdit edit);
  Future<MailRule> updateRule(String ruleId, MailRuleEdit edit);
  Future<MailRule> pauseRule(String ruleId);
  Future<MailRule> resumeRule(String ruleId);
  Future<void> deleteRule(String ruleId);

  // -- AI (`/api/v1/mail/ai`) -------------------------------------------------
  Future<MailAiStatus> aiStatus();
  Future<MailSummary> summarize(String accountId, String folder, int uid,
      {bool thread = false});
  Future<MailLabel> classify(String accountId, String folder, int uid);
  Future<List<MailLabel>> labels(
      String accountId, String folder, List<MailMessage> messages);
  Future<MailLabelPage> classifyPage(
      String accountId, String folder, List<int> uids);
  Future<MailDraftReply> draftReply(String accountId, String folder, int uid,
      {String kind = 'reply', String? instructions});

  /// Forwards to [to] (optionally with a note, or one written by AI): the
  /// draft with its `send_mail` approval; the same [key] answers the same.
  Future<MailDraft> forward(String accountId, String folder, int uid,
      {required List<String> to,
      String? note,
      bool writeNote = false,
      required String key});

  /// Archive (up to 200) or export (up to 100): the approval of exactly
  /// [uids]; nothing happens until an owner approves.
  Future<WorkspaceApproval> archiveMany(
      String accountId, String folder, List<int> uids);
  Future<WorkspaceApproval> exportMany(
      String accountId, String folder, List<int> uids);
}

class ApiMailCenterRepository implements MailCenterRepository {
  const ApiMailCenterRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/mail';
  static String _id(String value) => Uri.encodeComponent(value);
  String _path(String path, Map<String, String> query) =>
      Uri(path: path, queryParameters: query.isEmpty ? null : query).toString();

  @override
  Future<MailSetup> setup() async =>
      MailSetup.fromJson(JsonReader(await api.request('GET', '$_base/setup')));

  @override
  Future<MailAccount> connect(String address, String password,
          {String? displayName}) async =>
      MailAccount.fromJson(JsonReader(await api.request(
        'POST',
        '$_base/accounts',
        body: {
          'address': address.trim(),
          'password': password,
          if (displayName != null && displayName.trim().isNotEmpty)
            'display_name': displayName.trim(),
        },
      )));

  @override
  Future<MailAccount> updatePassword(String accountId, String password) async =>
      MailAccount.fromJson(JsonReader(await api.request(
        'PUT',
        '$_base/accounts/${_id(accountId)}/password',
        body: {'password': password},
      )));

  @override
  Future<MailAccount> check(String accountId) async =>
      MailAccount.fromJson(JsonReader(await api.request(
          'POST', '$_base/accounts/${_id(accountId)}/check')));

  @override
  Future<void> disconnect(String accountId) async =>
      api.request('DELETE', '$_base/accounts/${_id(accountId)}');

  @override
  Future<List<MailFolder>> folders(String accountId) async => JsonReader(
          await api.request('GET', '$_base/accounts/${_id(accountId)}/folders'))
      .objects('data', MailFolder.fromJson);

  @override
  Future<MailPage> messages(
    String accountId, {
    required String folder,
    int? before,
    String? query,
    bool unread = false,
    bool flagged = false,
  }) async {
    final value = JsonReader(await api.request(
      'GET',
      _path('$_base/accounts/${_id(accountId)}/messages', {
        'folder': folder,
        'limit': '25',
        if (before != null) 'before': '$before',
        if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
        if (unread) 'unread': 'true',
        if (flagged) 'flagged': 'true',
      }),
    ));
    return MailPage(
      value.objects('items', MailMessage.fromJson),
      value.optInt('total') ?? 0,
      value.optInt('next_before'),
    );
  }

  @override
  Future<MailDetail> read(String accountId, String folder, int uid) async =>
      MailDetail.fromJson(JsonReader(await api.request(
        'GET',
        _path('$_base/accounts/${_id(accountId)}/message',
            {'folder': folder, 'uid': '$uid'}),
      )));

  @override
  Future<void> setFlag(String accountId, String folder, int uid,
          {required String flag, required bool value}) async =>
      api.request('POST', '$_base/accounts/${_id(accountId)}/flags', body: {
        'folder': folder,
        'uids': [uid],
        'flag': flag,
        'value': value,
      });

  @override
  Future<void> move(
          String accountId, String folder, int uid, String to) async =>
      api.request('POST', '$_base/accounts/${_id(accountId)}/move', body: {
        'folder': folder,
        'uids': [uid],
        'to': to,
      });

  @override
  Future<void> setFlags(String accountId, String folder, List<int> uids,
          {required String flag, required bool value}) async =>
      api.request('POST', '$_base/accounts/${_id(accountId)}/flags', body: {
        'folder': folder,
        'uids': uids,
        'flag': flag,
        'value': value,
      });

  @override
  Future<void> moveMany(
          String accountId, String folder, List<int> uids, String to) async =>
      api.request('POST', '$_base/accounts/${_id(accountId)}/move', body: {
        'folder': folder,
        'uids': uids,
        'to': to,
      });

  @override
  Future<WorkspaceApproval?> deleteForGood(
      String accountId, String folder, List<int> uids) async {
    final value = JsonReader(await api.request(
        'POST', '$_base/accounts/${_id(accountId)}/delete',
        body: {'folder': folder, 'uids': uids}));
    final approval = value.optObject('approval');
    return approval == null ? null : workspaceApprovalFromJson(approval);
  }

  @override
  Future<List<MailDraft>> scheduled(String accountId) async =>
      JsonReader(await api.request(
              'GET',
              _path('$_base/scheduled',
                  {'account_id': accountId, 'limit': '50'})))
          .objects('data', MailDraft.fromJson);

  @override
  Future<MailDraft> getDraft(String draftId) async => MailDraft.fromJson(
      JsonReader(await api.request('GET', '$_base/drafts/${_id(draftId)}')));

  @override
  Future<MailDraft> cancelSchedule(String draftId) async =>
      MailDraft.fromJson(JsonReader(await api.request(
          'POST', '$_base/drafts/${_id(draftId)}/cancel-schedule')));

  @override
  Future<void> removeAttachment(String draftId, String fileId) async =>
      api.request(
          'DELETE', '$_base/drafts/${_id(draftId)}/attachments/${_id(fileId)}');

  @override
  Future<List<MailDraft>> drafts(String accountId) async =>
      JsonReader(await api.request('GET',
              _path('$_base/drafts', {'account_id': accountId, 'limit': '50'})))
          .objects('data', MailDraft.fromJson);

  @override
  Future<MailDraft> createDraft(String accountId,
          {String kind = 'new', String? folder, int? uid}) async =>
      MailDraft.fromJson(
          JsonReader(await api.request('POST', '$_base/drafts', body: {
        'account_id': accountId,
        'kind': kind,
        if (folder != null) 'folder': folder,
        if (uid != null) 'uid': uid,
      })));

  @override
  Future<MailDraft> updateDraft(String draftId, MailDraftEdit edit) async =>
      MailDraft.fromJson(JsonReader(await api.request(
          'PATCH', '$_base/drafts/${_id(draftId)}',
          body: edit.toJson())));

  @override
  Future<void> deleteDraft(String draftId) async =>
      api.request('DELETE', '$_base/drafts/${_id(draftId)}');

  @override
  Future<MailDraft> askToSend(String draftId, String key,
      {DateTime? sendAt}) async {
    final value = JsonReader(
        await api.request('POST', '$_base/drafts/${_id(draftId)}/send', body: {
      'idempotency_key': key,
      if (sendAt != null) 'send_at': sendAt.toUtc().toIso8601String(),
    }));
    return MailDraft.fromJson(value.object('draft'));
  }

  @override
  Future<String?> approve(WorkspaceApproval approval) async {
    final value = JsonReader(await api.request(
      'POST',
      '/api/v1/approvals/${_id(approval.id)}/approve',
      body: decisionBody(approval, null),
    ));
    return value.optObject('run')?.optString('result');
  }

  // -- sharing, identities, contacts ------------------------------------------

  @override
  Future<List<MailIdentity>> identities(String accountId) async =>
      JsonReader(await api.request(
              'GET', '$_base/accounts/${_id(accountId)}/identities'))
          .objects('data', MailIdentity.fromJson);

  @override
  Future<MailIdentity> createIdentity(
          String accountId, MailIdentityEdit edit) async =>
      MailIdentity.fromJson(JsonReader(await api.request(
          'POST', '$_base/accounts/${_id(accountId)}/identities',
          body: edit.toJson(create: true))));

  @override
  Future<MailIdentity> updateIdentity(
          String identityId, MailIdentityEdit edit) async =>
      MailIdentity.fromJson(JsonReader(await api.request(
          'PATCH', '$_base/identities/${_id(identityId)}',
          body: edit.toJson(create: false))));

  @override
  Future<void> deleteIdentity(String identityId) async =>
      api.request('DELETE', '$_base/identities/${_id(identityId)}');

  @override
  Future<List<MailGrant>> grants(String accountId) async => JsonReader(
          await api.request('GET', '$_base/accounts/${_id(accountId)}/grants'))
      .objects('data', MailGrant.fromJson);

  @override
  Future<List<MailShareCandidate>> shareCandidates(String accountId,
          {String? query}) async =>
      JsonReader(await api.request(
        'GET',
        _path('$_base/accounts/${_id(accountId)}/share-candidates', {
          if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
        }),
      ))
          .objects('data', MailShareCandidate.fromJson);

  @override
  Future<MailGrant> share(
          String accountId, String userId, String access) async =>
      MailGrant.fromJson(JsonReader(await api.request(
          'POST', '$_base/accounts/${_id(accountId)}/grants',
          body: {'grantee_user_id': userId, 'access': access})));

  @override
  Future<MailGrant> updateGrant(String grantId, String access) async =>
      MailGrant.fromJson(JsonReader(await api.request(
          'PATCH', '$_base/grants/${_id(grantId)}',
          body: {'access': access})));

  @override
  Future<void> removeGrant(String grantId) async =>
      api.request('DELETE', '$_base/grants/${_id(grantId)}');

  @override
  Future<MailContactPage> contacts(
      {String? query, String? cursor, int limit = 25}) async {
    final value = JsonReader(await api.request(
      'GET',
      _path('$_base/contacts', {
        'limit': '$limit',
        if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
        if (cursor != null) 'cursor': cursor,
      }),
    ));
    return MailContactPage(value.objects('items', MailContact.fromJson),
        value.optInt('total') ?? 0, value.optString('next_cursor'));
  }

  @override
  Future<MailContact> createContact(MailContactEdit edit) async =>
      MailContact.fromJson(JsonReader(
          await api.request('POST', '$_base/contacts', body: edit.toJson())));

  @override
  Future<MailContact> updateContact(
          String contactId, MailContactEdit edit) async =>
      MailContact.fromJson(JsonReader(await api.request(
          'PATCH', '$_base/contacts/${_id(contactId)}',
          body: edit.toJson())));

  @override
  Future<void> deleteContact(String contactId) async =>
      api.request('DELETE', '$_base/contacts/${_id(contactId)}');

  // -- AI ----------------------------------------------------------------------

  static const _ai = '$_base/ai';

  @override
  Future<MailAiStatus> aiStatus() async => MailAiStatus.fromJson(
      JsonReader(await api.request('GET', '$_ai/status')));

  @override
  Future<MailSummary> summarize(String accountId, String folder, int uid,
          {bool thread = false}) async =>
      MailSummary.fromJson(
          JsonReader(await api.request('POST', '$_ai/summarize', body: {
        'account_id': accountId,
        'folder': folder,
        'uid': uid,
        'thread': thread,
      })));

  @override
  Future<MailLabel> classify(String accountId, String folder, int uid) async =>
      MailLabel.fromJson(
          JsonReader(await api.request('POST', '$_ai/classify', body: {
            'account_id': accountId,
            'folder': folder,
            'uid': uid,
          })),
          uid: uid);

  @override
  Future<List<MailLabel>> labels(
      String accountId, String folder, List<MailMessage> messages) async {
    final shown = messages.take(100).toList();
    if (shown.isEmpty) return const [];
    return JsonReader(await api.request(
      'GET',
      _path('$_ai/labels', {
        'account_id': accountId,
        'folder': folder,
        'uids': shown.map((m) => '${m.uid}').join(','),
        'message_ids': shown.map((m) => m.messageId ?? '').join('\t'),
      }),
    ))
        .objects('data', MailLabel.fromJson);
  }

  @override
  Future<MailLabelPage> classifyPage(
          String accountId, String folder, List<int> uids) async =>
      MailLabelPage.fromJson(
          JsonReader(await api.request('POST', '$_ai/classify-page', body: {
        'account_id': accountId,
        'folder': folder,
        'uids': uids.take(25).toList(),
      })));

  @override
  Future<MailDraftReply> draftReply(String accountId, String folder, int uid,
          {String kind = 'reply', String? instructions}) async =>
      MailDraftReply.fromJson(
          JsonReader(await api.request('POST', '$_ai/draft-reply', body: {
        'account_id': accountId,
        'folder': folder,
        'uid': uid,
        'kind': kind,
        if (instructions != null && instructions.trim().isNotEmpty)
          'instructions': instructions.trim(),
      })));

  @override
  Future<MailDraft> forward(String accountId, String folder, int uid,
      {required List<String> to,
      String? note,
      bool writeNote = false,
      required String key}) async {
    final value = JsonReader(await api.request('POST', '$_ai/forward', body: {
      'account_id': accountId,
      'folder': folder,
      'uid': uid,
      'to': to,
      if (note != null && note.trim().isNotEmpty) 'note': note.trim(),
      'write_note': writeNote,
      'idempotency_key': key,
    }));
    return MailDraft.fromJson(value.object('draft'));
  }

  Future<WorkspaceApproval> _owner(
      String action, String accountId, String folder, List<int> uids) async {
    final value = JsonReader(await api.request('POST', '$_ai/$action',
        body: {'account_id': accountId, 'folder': folder, 'uids': uids}));
    return workspaceApprovalFromJson(value.object('approval'));
  }

  @override
  Future<WorkspaceApproval> archiveMany(
          String accountId, String folder, List<int> uids) =>
      _owner('archive', accountId, folder, uids);

  @override
  Future<WorkspaceApproval> exportMany(
          String accountId, String folder, List<int> uids) =>
      _owner('export', accountId, folder, uids);

  // -- retention, sync and forwarding rules ------------------------------------

  @override
  Future<MailSettings> settings(String accountId) async =>
      MailSettings.fromJson(JsonReader(await api.request(
          'GET', '$_base/accounts/${_id(accountId)}/settings')));

  @override
  Future<WorkspaceApproval> changeSettings(
      String accountId, MailSettingsEdit edit, int baseVersion) async {
    final value = JsonReader(await api.request(
        'POST', '$_base/accounts/${_id(accountId)}/settings',
        body: edit.toJson(baseVersion)));
    return workspaceApprovalFromJson(value.object('approval'));
  }

  @override
  Future<void> withdrawSettings(String accountId) async => api.request(
      'DELETE', '$_base/accounts/${_id(accountId)}/settings/pending');

  @override
  Future<List<MailRule>> rules(String accountId) async => JsonReader(
          await api.request('GET', '$_base/accounts/${_id(accountId)}/rules'))
      .objects('data', MailRule.fromJson);

  MailRule _rule(JsonMap value) {
    final reader = JsonReader(value);
    return MailRule.fromJson(reader.optObject('rule') ?? reader);
  }

  @override
  Future<MailRule> createRule(String accountId, MailRuleEdit edit) async =>
      _rule(await api.request('POST', '$_base/accounts/${_id(accountId)}/rules',
          body: edit.toJson()));

  @override
  Future<MailRule> updateRule(String ruleId, MailRuleEdit edit) async =>
      _rule(await api.request('PATCH', '$_base/rules/${_id(ruleId)}',
          body: edit.toJson()));

  @override
  Future<MailRule> pauseRule(String ruleId) async =>
      _rule(await api.request('POST', '$_base/rules/${_id(ruleId)}/pause'));

  @override
  Future<MailRule> resumeRule(String ruleId) async =>
      _rule(await api.request('POST', '$_base/rules/${_id(ruleId)}/resume'));

  @override
  Future<void> deleteRule(String ruleId) async =>
      api.request('DELETE', '$_base/rules/${_id(ruleId)}');
}
