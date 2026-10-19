import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../workspace/workspace_models.dart';
import 'email_models.dart';
import 'email_repository.dart';

enum InboxPhase { idle, loading, ready, error, denied }

/// The inbox's state (docs/email.md): mailboxes, the unified list with paging
/// and search, and one opened message with its drafts.
class EmailController extends ChangeNotifier {
  EmailController(this.repository);
  final EmailRepository repository;

  InboxPhase phase = InboxPhase.idle;
  String? error;
  List<EmailAccount> accounts = const [];
  List<EmailMessage> messages = const [];
  String? nextCursor;
  bool loadingMore = false;
  String query = '';
  bool unreadOnly = false;

  EmailDetail? detail;
  bool detailLoading = false;
  String? detailError;

  /// The approval drafting a reply waits for (a workspace policy), shown
  /// with its payload hash until an owner decides it; null otherwise.
  WorkspaceApproval? draftApproval;

  /// The action in progress on the opened message (draft, ask, approve, read).
  String? busy;

  /// The last action's outcome, said once by the screen.
  String? notice;
  bool noticeIsError = false;

  bool get hasFilters => query.trim().isNotEmpty || unreadOnly;

  String _describe(Object e) => e is ApiException ? e.message : '$e';

  Future<void> load() async {
    phase = InboxPhase.loading;
    error = null;
    notifyListeners();
    try {
      accounts = await repository.accounts();
      final page = await repository.messages(
        query: query,
        unreadOnly: unreadOnly,
      );
      messages = page.items;
      nextCursor = page.nextCursor;
      phase = InboxPhase.ready;
    } on ApiException catch (e) {
      phase = e.statusCode == 403 ? InboxPhase.denied : InboxPhase.error;
      error = e.message;
    } on Object catch (e) {
      phase = InboxPhase.error;
      error = _describe(e);
    }
    notifyListeners();
  }

  Future<void> search(String text, {bool? unread}) async {
    query = text;
    if (unread != null) unreadOnly = unread;
    await load();
  }

  Future<void> loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.messages(
        query: query,
        unreadOnly: unreadOnly,
        before: cursor,
      );
      messages = [...messages, ...page.items];
      nextCursor = page.nextCursor;
    } on Object catch (e) {
      _say(_describe(e), error: true);
    }
    loadingMore = false;
    notifyListeners();
  }

  Future<void> open(String messageId) async {
    detail = null;
    draftApproval = null;
    detailError = null;
    detailLoading = true;
    notifyListeners();
    try {
      detail = await repository.open(messageId);
    } on Object catch (e) {
      detailError = _describe(e);
    }
    detailLoading = false;
    notifyListeners();
  }

  Future<void> toggleRead() async {
    final current = detail;
    if (current == null) return;
    await _run('read', () async {
      final updated = await repository.markRead(
        current.message.id,
        !current.message.isRead,
      );
      final message =
          updated ?? current.message.copyWith(isRead: !current.message.isRead);
      detail = EmailDetail(
        message: message,
        body: current.body,
        bodyError: current.bodyError,
        truncated: current.truncated,
        attachments: current.attachments,
        drafts: current.drafts,
      );
      messages = [
        for (final item in messages) item.id == message.id ? message : item,
      ];
    });
  }

  Future<void> draftReply({String? instructions}) async {
    final current = detail;
    if (current == null) return;
    await _run('draft', () async {
      draftApproval = null;
      try {
        final draft = await repository.draftReply(
          current.message.id,
          instructions: instructions,
        );
        _replaceDraft(draft, insert: true);
      } on DraftWaitsForApproval catch (waiting) {
        // Not a failure: the approval card below the reply says what to do.
        draftApproval = waiting.approval;
        _say(waiting.summary);
      }
    });
  }

  Future<void> saveDraft(EmailDraft draft, String body) =>
      _run('save:${draft.id}', () async {
        _replaceDraft(await repository.updateDraft(draft.id, body: body));
      });

  Future<void> askToSend(EmailDraft draft, {String? editedBody}) =>
      _run('ask:${draft.id}', () async {
        if (editedBody != null && editedBody != draft.body) {
          await repository.updateDraft(draft.id, body: editedBody);
        }
        _replaceDraft(await repository.askToSend(draft.id));
      });

  Future<void> approve(WorkspaceApproval approval) =>
      _run('approve:${approval.id}', () async {
        final result = await repository.approve(approval);
        if (draftApproval?.id == approval.id) draftApproval = null;
        final opened = detail?.message.id;
        if (opened != null) detail = await repository.open(opened);
        _say(result ?? 'Sent.');
      });

  void _replaceDraft(EmailDraft draft, {bool insert = false}) {
    final current = detail;
    if (current == null) return;
    final drafts = insert
        ? [draft, ...current.drafts]
        : [for (final d in current.drafts) d.id == draft.id ? draft : d];
    detail = EmailDetail(
      message: current.message,
      body: current.body,
      bodyError: current.bodyError,
      truncated: current.truncated,
      attachments: current.attachments,
      drafts: drafts,
    );
  }

  Future<void> _run(String kind, Future<void> Function() action) async {
    busy = kind;
    notice = null;
    notifyListeners();
    try {
      await action();
    } on Object catch (e) {
      _say(_describe(e), error: true);
    }
    busy = null;
    notifyListeners();
  }

  void _say(String text, {bool error = false}) {
    notice = text;
    noticeIsError = error;
  }

  void clearNotice() {
    notice = null;
  }
}
