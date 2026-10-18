import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../workspace/workspace_models.dart';
import 'mail_center_models.dart';

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
  Future<List<MailDraft>> drafts(String accountId);
  Future<MailDraft> createDraft(String accountId,
      {String kind = 'new', String? folder, int? uid});
  Future<MailDraft> updateDraft(String draftId, MailDraftEdit edit);
  Future<void> deleteDraft(String draftId);

  /// Asks to send with [key] (one key per attempt; a retry reuses it).
  Future<MailDraft> askToSend(String draftId, String key);

  /// Approves with the displayed payload hash; the run's result sentence.
  Future<String?> approve(WorkspaceApproval approval);
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
  Future<MailDraft> askToSend(String draftId, String key) async {
    final value = JsonReader(await api.request(
        'POST', '$_base/drafts/${_id(draftId)}/send',
        body: {'idempotency_key': key}));
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
}
