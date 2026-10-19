import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../business/support_controller.dart';
import '../workspace/workspace_models.dart';
import 'email_models.dart';
import 'email_repository.dart';

enum InboxPhase { idle, loading, ready, error, denied }

/// The inbox's state (docs/email.md): mailboxes, the unified list with paging
/// and search, and one opened message with its drafts.
class EmailController extends ChangeNotifier {
  EmailController(this.repository, {this.support});
  final EmailRepository repository;

  /// Business support tickets, when wired: "Create ticket" on a message
  /// (docs/business.md#tickets-from-an-inbox). Null hides the action.
  final SupportController? support;

  InboxPhase phase = InboxPhase.idle;
  String? error;
  List<EmailAccount> accounts = const [];
  List<EmailMessage> messages = const [];
  String? nextCursor;
  bool loadingMore = false;
  String query = '';
  bool unreadOnly = false;

  /// New emails saved as drafts and not sent yet (best effort: a failure
  /// leaves the list as it was).
  List<EmailDraft> newDrafts = const [];

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

  /// Mailboxes a new email can be sent from (not waiting for a reconnect).
  List<EmailAccount> get sendableAccounts => [
        for (final account in accounts)
          if (!account.needsReconnect) account,
      ];

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
      unawaited(loadDrafts());
    } on ApiException catch (e) {
      phase = e.statusCode == 403 ? InboxPhase.denied : InboxPhase.error;
      error = e.message;
    } on Object catch (e) {
      phase = InboxPhase.error;
      error = _describe(e);
    }
    notifyListeners();
  }

  Future<void> loadDrafts() async {
    try {
      newDrafts = [
        for (final d in await repository.drafts())
          if (d.isNew && d.waiting) d
      ];
    } on Object {
      // The drafts list is a shortcut; the inbox works without it.
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

/// A brand-new email (docs/email.md): saved as a draft in one mailbox, then
/// sent only after an owner approves the exact message with its payload hash.
class ComposeController extends ChangeNotifier {
  ComposeController(this.repository);
  final EmailRepository repository;

  /// The saved draft; null until the first save.
  EmailDraft? draft;

  /// What the paused drafting was asked to save, to find the draft again once
  /// an owner approves it.
  EmailDraftFields? _pendingFields;

  /// Continues a draft saved earlier (opened from the inbox's drafts list).
  void adopt(EmailDraft existing) {
    draft = existing;
    saved = existing.fields;
  }

  /// Throws the saved draft away. True when it is gone.
  Future<bool> discard() => _run('discard', () async {
        final current = draft;
        if (current == null) return true;
        await repository.discardDraft(current.id);
        draft = null;
        saved = null;
        return true;
      });

  /// The fields as last saved, to tell whether the screen has unsaved edits.
  EmailDraftFields? saved;

  /// The approval drafting waits for (a workspace policy); null otherwise.
  WorkspaceApproval? draftApproval;

  /// The run's sentence once drafting itself was approved.
  String? draftingResult;

  /// `save`, `ask` or `approve` while that action runs.
  String? busy;

  /// The last failure, as the API said it.
  String? error;

  /// The approved run's sentence once the email is sent.
  String? result;

  bool _disposed = false;

  bool get sent => draft?.sent ?? false;

  /// The pending send approval, when one waits for an owner.
  WorkspaceApproval? get sendApproval {
    final approval = draft?.approval;
    return approval != null && approval.status == 'pending' ? approval : null;
  }

  /// Creates the draft on the first save, then saves edits. True when the
  /// draft now holds [fields].
  Future<bool> save(EmailDraftFields fields) =>
      _run('save', () => _save(fields));

  /// Saves [fields] when they changed, then asks to send.
  Future<bool> askToSend(EmailDraftFields fields) => _run('ask', () async {
        if (!await _save(fields)) return false;
        draft = await repository.askToSend(draft!.id);
        return true;
      });

  Future<bool> approve(WorkspaceApproval approval) => _run('approve', () async {
        final sentence = await repository.approve(approval);
        if (draftApproval?.id == approval.id) {
          draftApproval = null;
          draftingResult = sentence ?? '';
          await _recoverDraft();
          return true;
        }
        result = sentence;
        draft = draft?.copyWith(status: 'sent');
        return true;
      });

  /// Approving the paused drafting created the draft on the server: find it
  /// so the next save edits it instead of creating a second one.
  Future<void> _recoverDraft() async {
    final wanted = _pendingFields;
    if (wanted == null) return;
    final found = (await repository.drafts()).where(
      (d) => d.isNew && d.waiting && d.fields == wanted,
    );
    if (found.isEmpty) return;
    adopt(found.first);
    _pendingFields = null;
  }

  Future<bool> _save(EmailDraftFields fields) async {
    final current = draft;
    if (current != null && saved == fields) return true;
    if (current == null) {
      draftApproval = null;
      draftingResult = null;
      try {
        draft = await repository.createDraft(fields);
      } on DraftWaitsForApproval catch (waiting) {
        // Not a failure: the approval card says what happens next.
        draftApproval = waiting.approval;
        _pendingFields = fields;
        return false;
      }
    } else {
      draft = await repository.updateDraft(current.id, fields: fields);
    }
    saved = fields;
    return true;
  }

  Future<bool> _run(String kind, Future<bool> Function() action) async {
    if (busy != null) return false;
    busy = kind;
    error = null;
    _notify();
    var ok = false;
    try {
      ok = await action();
    } on ApiException catch (e) {
      error = e.readable;
    } on Object catch (e) {
      error = '$e';
    }
    busy = null;
    _notify();
    return ok;
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
