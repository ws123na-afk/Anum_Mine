import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../workspace/api_workspace_repository.dart'
    show workspaceApprovalFromJson;
import '../workspace/workspace_models.dart';
import 'email_models.dart';

/// The unified inbox over the API (docs/email.md). Owners and members read
/// and draft; sending waits for an owner's approval of the exact message.
/// Mailboxes are connected from the web app (the provider's sign-in).
/// Drafting a reply is itself waiting for an owner's approval (a workspace
/// policy on the draft tool): no draft yet, only the approval to decide.
class DraftWaitsForApproval implements Exception {
  const DraftWaitsForApproval(this.summary, this.approval);
  final String summary;
  final WorkspaceApproval approval;

  @override
  String toString() => summary;
}

abstract interface class EmailRepository {
  Future<List<EmailAccount>> accounts();
  Future<EmailPage> messages({
    String? query,
    bool unreadOnly = false,
    String? before,
  });
  Future<EmailDetail> open(String messageId);
  Future<EmailMessage?> markRead(String messageId, bool read);

  /// Throws [DraftWaitsForApproval] when drafting itself needs approval.
  Future<EmailDraft> draftReply(String messageId, {String? instructions});

  /// Starts a brand-new email in [EmailDraftFields.accountId]'s mailbox.
  /// Empty To and body are allowed here; asking to send refuses them.
  /// Throws [DraftWaitsForApproval] when drafting itself needs approval.
  Future<EmailDraft> createDraft(EmailDraftFields fields);

  /// Saves the reply text ([body]) or, for a new email, every field
  /// ([fields]).
  Future<EmailDraft> updateDraft(
    String draftId, {
    String? body,
    EmailDraftFields? fields,
  });

  /// Asks to send: the pending approval to show (tool, arguments, hash).
  Future<EmailDraft> askToSend(String draftId);

  /// Approves with the displayed payload hash; the run's result sentence.
  Future<String?> approve(WorkspaceApproval approval);
}

class ApiEmailRepository implements EmailRepository {
  const ApiEmailRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/email';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<List<EmailAccount>> accounts() async =>
      JsonReader(await api.request('GET', '$_base/accounts'))
          .objects('data', EmailAccount.fromJson);

  @override
  Future<EmailPage> messages({
    String? query,
    bool unreadOnly = false,
    String? before,
  }) async {
    final params = <String, String>{
      'limit': '25',
      if (query != null && query.trim().isNotEmpty) 'q': query.trim(),
      if (unreadOnly) 'unread': 'true',
      if (before != null) 'before': before,
    };
    final value = JsonReader(
      await api.request(
        'GET',
        Uri(path: '$_base/messages', queryParameters: params).toString(),
      ),
    );
    return EmailPage(
      value.objects('items', EmailMessage.fromJson),
      value.optString('next_cursor'),
    );
  }

  @override
  Future<EmailDetail> open(String messageId) async => EmailDetail.fromJson(
        JsonReader(
            await api.request('GET', '$_base/messages/${_id(messageId)}')),
      );

  @override
  Future<EmailMessage?> markRead(String messageId, bool read) async {
    final value = JsonReader(
      await api.request(
        'POST',
        '$_base/messages/${_id(messageId)}/read',
        body: {'read': read},
      ),
    );
    final message = value.optObject('message');
    return message == null ? null : EmailMessage.fromJson(message);
  }

  @override
  Future<EmailDraft> draftReply(
    String messageId, {
    String? instructions,
  }) async {
    final value = JsonReader(
      await api.request(
        'POST',
        '$_base/messages/${_id(messageId)}/drafts',
        body: {
          if (instructions != null && instructions.trim().isNotEmpty)
            'instructions': instructions.trim(),
        },
      ),
    );
    return _drafted(value);
  }

  /// The drafting answer: the draft, or the approval drafting waits for.
  static EmailDraft _drafted(JsonReader value) {
    final draft = value.optObject('draft');
    final approval = value.optObject('approval');
    if (draft == null && approval != null) {
      throw DraftWaitsForApproval(
        value.optString('summary') ?? 'Drafting waits for approval.',
        workspaceApprovalFromJson(approval),
      );
    }
    if (draft == null) {
      throw ApiException(
        202,
        value.optString('summary') ?? 'Drafting waits for approval.',
      );
    }
    return EmailDraft.fromJson(draft);
  }

  @override
  Future<EmailDraft> createDraft(EmailDraftFields fields) async => _drafted(
        JsonReader(
          await api.request('POST', '$_base/drafts', body: fields.toCreate()),
        ),
      );

  @override
  Future<EmailDraft> updateDraft(
    String draftId, {
    String? body,
    EmailDraftFields? fields,
  }) async =>
      EmailDraft.fromJson(
        JsonReader(
          await api.request(
            'PATCH',
            '$_base/drafts/${_id(draftId)}',
            body: {
              ...?fields?.toPatch(),
              if (body != null) 'body': body,
            },
          ),
        ),
      );

  @override
  Future<EmailDraft> askToSend(String draftId) async => EmailDraft.fromJson(
        JsonReader(
          await api.request(
              'POST', '$_base/drafts/${_id(draftId)}/send-approval'),
        ).object('draft'),
      );

  @override
  Future<String?> approve(WorkspaceApproval approval) async {
    final value = JsonReader(
      await api.request(
        'POST',
        '/api/v1/approvals/${_id(approval.id)}/approve',
        body: decisionBody(approval, null),
      ),
    );
    return value.optObject('run')?.optString('result');
  }
}
