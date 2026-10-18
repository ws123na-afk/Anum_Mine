import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../workspace/workspace_models.dart';
import 'mail_center_models.dart';
import 'mail_center_repository.dart';

/// Where one part of the Email Center stands. `offline` covers both the phone
/// being offline and the mail server not answering (HTTP 503).
enum MailPhase { idle, loading, ready, error, denied, offline }

MailPhase phaseFor(Object error) => switch (error) {
      ApiException(statusCode: 403) => MailPhase.denied,
      ApiException(statusCode: 0 || 503) => MailPhase.offline,
      _ => MailPhase.error,
    };

String describe(Object error) => switch (error) {
      ApiException() => error.readable,
      _ => '$error',
    };

/// The Email Center's state on the phone (docs/email.md#email-center):
/// mailboxes, the open mailbox's folders and drafts, one folder's messages
/// with paging and search, the opened message, and the draft being written.
class MailCenterController extends ChangeNotifier {
  MailCenterController(this.repository, {DateTime Function()? clock})
      : _clock = clock ?? DateTime.now;
  final MailCenterRepository repository;
  final DateTime Function() _clock;

  MailPhase setupPhase = MailPhase.idle;
  String? setupError;
  MailSetup? setup;

  MailAccount? account;
  MailPhase foldersPhase = MailPhase.idle;
  String? foldersError;
  List<MailFolder> folders = const [];
  List<MailDraft> drafts = const [];

  MailFolder? folder;
  MailPhase listPhase = MailPhase.idle;
  String? listError;
  List<MailMessage> messages = const [];
  int total = 0;
  int? nextBefore;
  bool loadingMore = false;
  String query = '';
  bool unreadOnly = false;
  bool flaggedOnly = false;

  MailPhase detailPhase = MailPhase.idle;
  String? detailError;
  MailDetail? detail;

  /// The message last opened, so a failed read can be tried again.
  MailMessage? opened;

  /// The draft open in the composer.
  MailDraft? draft;

  /// The action in progress (connect, password, move, send, approve, ...).
  String? busy;

  /// The last action's outcome, said once by the screen: a string key
  /// (`mc.n.*`) or the API's own sentence, which the screen shows as is.
  String? notice;
  bool noticeIsError = false;

  // One idempotency key per send attempt of a draft: a retry after a lost
  // answer reuses it, so the same message is never asked for twice.
  final _sendKeys = <String, String>{};

  bool get hasFilters => query.trim().isNotEmpty || unreadOnly || flaggedOnly;
  List<MailAccount> get accounts => setup?.accounts ?? const [];

  // -- mailboxes -----------------------------------------------------------

  Future<void> loadSetup() async {
    setupPhase = MailPhase.loading;
    setupError = null;
    notifyListeners();
    try {
      setup = await repository.setup();
      final open = account;
      if (open != null) {
        account = accounts.where((a) => a.id == open.id).firstOrNull;
      }
      setupPhase = MailPhase.ready;
    } on Object catch (e) {
      setupPhase = phaseFor(e);
      setupError = describe(e);
    }
    notifyListeners();
  }

  Future<bool> connect(String address, String password,
      {String? displayName}) async {
    var ok = false;
    await _run('connect', () async {
      await repository.connect(address, password, displayName: displayName);
      ok = true;
      await loadSetup();
      _say('mc.n.connected');
    });
    return ok;
  }

  Future<bool> updatePassword(MailAccount target, String password) async {
    var ok = false;
    await _run('password', () async {
      final updated = await repository.updatePassword(target.id, password);
      ok = true;
      _replaceAccount(updated);
      _say('mc.n.password');
    });
    return ok;
  }

  Future<void> check(MailAccount target) => _run('check', () async {
        final updated = await repository.check(target.id);
        _replaceAccount(updated);
        _say(
            updated.healthy
                ? 'mc.n.checked'
                : (updated.lastError ?? 'mc.n.checkFailed'),
            error: !updated.healthy);
      });

  Future<bool> disconnect(MailAccount target) async {
    var ok = false;
    await _run('disconnect', () async {
      await repository.disconnect(target.id);
      ok = true;
      if (account?.id == target.id) account = null;
      await loadSetup();
      _say('mc.n.removed');
    });
    return ok;
  }

  void _replaceAccount(MailAccount updated) {
    final current = setup;
    if (current != null) {
      setup = MailSetup(
        enabled: current.enabled,
        configured: current.configured,
        tenantAllowed: current.tenantAllowed,
        canAct: current.canAct,
        allowedDomains: current.allowedDomains,
        maxRecipients: current.maxRecipients,
        sendPerHour: current.sendPerHour,
        accounts: [
          for (final a in current.accounts) a.id == updated.id ? updated : a
        ],
      );
    }
    if (account?.id == updated.id) account = updated;
  }

  // -- folders and drafts ----------------------------------------------------

  Future<void> openAccount(MailAccount target) async {
    account = target;
    folders = const [];
    drafts = const [];
    await loadFolders();
  }

  Future<void> loadFolders() async {
    final open = account;
    if (open == null) return;
    foldersPhase = MailPhase.loading;
    foldersError = null;
    notifyListeners();
    try {
      folders = await repository.folders(open.id);
      foldersPhase = MailPhase.ready;
    } on Object catch (e) {
      foldersPhase = phaseFor(e);
      foldersError = describe(e);
      if (e is ApiException && e.statusCode == 409) await _refreshAccount();
    }
    try {
      drafts = await repository.drafts(open.id);
    } on Object {
      // The folders' state already says what failed; drafts stay as they were.
    }
    notifyListeners();
  }

  /// After a refused password the API marks the mailbox; read it again so the
  /// screens offer "Enter the password again".
  Future<void> _refreshAccount() async {
    try {
      setup = await repository.setup();
      final open = account;
      if (open != null) {
        account = accounts.where((a) => a.id == open.id).firstOrNull ?? open;
      }
    } on Object {
      // Keep what we had; the error on screen already says what failed.
    }
  }

  MailFolder? folderWithRole(String role) =>
      folders.where((f) => f.role == role).firstOrNull;

  // -- messages ------------------------------------------------------------

  Future<void> openFolder(MailFolder target) async {
    folder = target;
    messages = const [];
    nextBefore = null;
    query = '';
    unreadOnly = false;
    flaggedOnly = false;
    await loadMessages();
  }

  Future<void> loadMessages() async {
    final open = account, inFolder = folder;
    if (open == null || inFolder == null) return;
    listPhase = MailPhase.loading;
    listError = null;
    notifyListeners();
    try {
      final page = await repository.messages(open.id,
          folder: inFolder.id,
          query: query,
          unread: unreadOnly,
          flagged: flaggedOnly);
      messages = page.items;
      total = page.total;
      nextBefore = page.nextBefore;
      listPhase = MailPhase.ready;
    } on Object catch (e) {
      listPhase = phaseFor(e);
      listError = describe(e);
      if (e is ApiException && e.statusCode == 409) await _refreshAccount();
    }
    notifyListeners();
  }

  Future<void> search(String text, {bool? unread, bool? flagged}) async {
    query = text;
    if (unread != null) unreadOnly = unread;
    if (flagged != null) flaggedOnly = flagged;
    await loadMessages();
  }

  Future<void> loadMore() async {
    final open = account, inFolder = folder, before = nextBefore;
    if (open == null || inFolder == null || before == null || loadingMore) {
      return;
    }
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.messages(open.id,
          folder: inFolder.id,
          before: before,
          query: query,
          unread: unreadOnly,
          flagged: flaggedOnly);
      messages = [...messages, ...page.items];
      nextBefore = page.nextBefore;
    } on Object catch (e) {
      _say(describe(e), error: true);
    }
    loadingMore = false;
    notifyListeners();
  }

  // -- one message -----------------------------------------------------------

  Future<void> read(MailMessage message) async {
    final open = account;
    if (open == null) return;
    opened = message;
    detail = null;
    detailError = null;
    detailPhase = MailPhase.loading;
    notifyListeners();
    try {
      detail = await repository.read(open.id, message.folder, message.uid);
      detailPhase = MailPhase.ready;
      // Opening marks it read on the server; the list follows.
      _replaceMessage(message.copyWith(seen: true));
    } on Object catch (e) {
      detailPhase = phaseFor(e);
      detailError = describe(e);
    }
    notifyListeners();
  }

  Future<void> retryRead() async {
    final message = opened;
    if (message != null) await read(message);
  }

  void _replaceMessage(MailMessage updated) {
    messages = [
      for (final m in messages)
        m.uid == updated.uid && m.folder == updated.folder ? updated : m
    ];
    final current = detail;
    if (current != null && current.message.uid == updated.uid) {
      detail = MailDetail(
        message: updated,
        replyTo: current.replyTo,
        text: current.text,
        textTruncated: current.textTruncated,
        hasHtml: current.hasHtml,
        textFromHtml: current.textFromHtml,
        remoteImagesBlocked: current.remoteImagesBlocked,
        inlineImagesHidden: current.inlineImagesHidden,
        attachments: current.attachments,
      );
    }
  }

  Future<void> toggleFlag(MailMessage message) => _run('flag', () async {
        await repository.setFlag(account!.id, message.folder, message.uid,
            flag: 'flagged', value: !message.flagged);
        _replaceMessage(message.copyWith(flagged: !message.flagged));
      });

  Future<void> markUnread(MailMessage message) => _run('seen', () async {
        await repository.setFlag(account!.id, message.folder, message.uid,
            flag: 'seen', value: false);
        _replaceMessage(message.copyWith(seen: false));
        _say('mc.n.unread');
      });

  /// Moves [message] to a role (archive, junk, trash, inbox); true when done.
  Future<bool> move(MailMessage message, String role) async {
    var ok = false;
    await _run('move', () async {
      await repository.move(account!.id, message.folder, message.uid, role);
      ok = true;
      messages = [
        for (final m in messages)
          if (!(m.uid == message.uid && m.folder == message.folder)) m
      ];
      total = total > 0 ? total - 1 : 0;
      _say(switch (role) {
        'archive' => 'mc.n.archived',
        'trash' => 'mc.n.trashed',
        'junk' => 'mc.n.junked',
        _ => 'mc.n.inboxed',
      });
    });
    return ok;
  }

  // -- writing and sending ----------------------------------------------------

  /// Starts a new message, reply, reply to all or forward; true when the
  /// draft is ready for the composer.
  Future<bool> startDraft(String kind, {MailMessage? source}) async {
    final open = account;
    if (open == null) return false;
    var ok = false;
    await _run('draft', () async {
      draft = await repository.createDraft(open.id,
          kind: kind, folder: source?.folder, uid: source?.uid);
      ok = true;
      _upsertDraft(draft!);
    });
    return ok;
  }

  void editDraft(MailDraft existing) {
    draft = existing;
    notifyListeners();
  }

  Future<bool> saveDraft(MailDraftEdit edit) async {
    final current = draft;
    if (current == null) return false;
    var ok = false;
    await _run('save', () async {
      draft = await repository.updateDraft(current.id, edit);
      ok = true;
      _upsertDraft(draft!);
      _say('mc.n.saved');
    });
    return ok;
  }

  /// Saves [edit], then asks to send: an owner approves the exact message.
  Future<void> askToSend(MailDraftEdit edit) async {
    final current = draft;
    if (current == null) return;
    await _run('send', () async {
      var latest = current;
      if (_changed(current, edit)) {
        latest = await repository.updateDraft(current.id, edit);
        _sendKeys.remove(current.id);
      }
      final key = _sendKeys[current.id] ??=
          'mobile:${current.id}:${_clock().microsecondsSinceEpoch}';
      draft = await repository.askToSend(latest.id, key);
      _upsertDraft(draft!);
      _say(draft!.waiting ? 'mc.n.waiting' : 'mc.n.asked');
    });
  }

  bool _changed(MailDraft d, MailDraftEdit e) =>
      d.subject != e.subject ||
      d.body != e.body ||
      !listEquals(d.to, e.to) ||
      !listEquals(d.cc, e.cc) ||
      !listEquals(d.bcc, e.bcc);

  Future<void> approve(WorkspaceApproval approval) => _run('approve', () async {
        final result = await repository.approve(approval);
        final open = account;
        if (open != null) {
          drafts = await repository.drafts(open.id);
          final current = draft;
          if (current != null) {
            draft =
                drafts.where((d) => d.id == current.id).firstOrNull ?? current;
          }
        }
        _sendKeys.remove(draft?.id);
        _say(result ?? 'mc.n.approved');
      });

  Future<bool> deleteDraft(MailDraft target) async {
    var ok = false;
    await _run('delete', () async {
      await repository.deleteDraft(target.id);
      ok = true;
      drafts = [
        for (final d in drafts)
          if (d.id != target.id) d
      ];
      if (draft?.id == target.id) draft = null;
      _say('mc.n.draftDeleted');
    });
    return ok;
  }

  void _upsertDraft(MailDraft updated) {
    final known = drafts.any((d) => d.id == updated.id);
    drafts = known
        ? [for (final d in drafts) d.id == updated.id ? updated : d]
        : [updated, ...drafts];
  }

  /// Forgets everything (sign-out or another workspace).
  void reset() {
    setupPhase = foldersPhase = listPhase = detailPhase = MailPhase.idle;
    setup = account = folder = detail = opened = draft = null;
    setupError = foldersError = listError = detailError = notice = busy = null;
    folders = const [];
    drafts = const [];
    messages = const [];
    total = 0;
    nextBefore = null;
    query = '';
    unreadOnly = flaggedOnly = false;
    _sendKeys.clear();
    notifyListeners();
  }

  // -- plumbing --------------------------------------------------------------

  Future<void> _run(String kind, Future<void> Function() action) async {
    busy = kind;
    notice = null;
    notifyListeners();
    try {
      await action();
    } on Object catch (e) {
      _say(describe(e), error: true);
    }
    busy = null;
    notifyListeners();
  }

  void _say(String text, {bool error = false}) {
    notice = text;
    noticeIsError = error;
  }

  /// The notice to show once (cleared when read).
  String? takeNotice() {
    final text = notice;
    notice = null;
    return text;
  }
}
