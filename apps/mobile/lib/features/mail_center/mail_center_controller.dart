import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../workspace/files_repository.dart';
import '../workspace/workspace_models.dart';
import 'mail_all_inboxes.dart';
import 'mail_center_models.dart';
import 'mail_center_repository.dart';
import 'mail_center_transfer.dart';
import 'mail_directory.dart';
import 'mail_directory_models.dart';

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
      UnsupportedError(:final message) => message ?? '$error',
      _ => '$error',
    };

/// One file on its way to a draft: progress and a stop button.
class MailUpload {
  MailUpload(this.name, this.total);
  final String name;
  final int total;
  int sent = 0;
  final cancellation = UploadCancellation();
}

/// A controller whose last action's outcome the screen says once: a string
/// key (`mc.n.*`) or the API's own sentence, which the screen shows as is.
mixin MailNoticeSource on ChangeNotifier {
  String? notice;
  bool noticeIsError = false;

  void say(String text, {bool error = false}) {
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

/// The Email Center's state on the phone (docs/email.md#email-center):
/// mailboxes, the open mailbox's folders and drafts, one folder's messages
/// with paging and search, the opened message, and the draft being written.
class MailCenterController extends ChangeNotifier with MailNoticeSource {
  MailCenterController(this.repository,
      {DateTime Function()? clock,
      this.transfer = const NoMailFileTransfer(),
      MailAllInboxesRepository? allInboxesRepository})
      : _clock = clock ?? DateTime.now,
        allInboxes = allInboxesRepository == null
            ? null
            : MailAllInboxesController(allInboxesRepository);
  final MailCenterRepository repository;
  final MailFileTransfer transfer;
  final DateTime Function() _clock;

  /// All inboxes over every readable mailbox (docs/email.md#all-inboxes);
  /// null when the app was built without it.
  final MailAllInboxesController? allInboxes;

  /// Contacts, shares and identities (same repository).
  late final MailDirectoryController directory =
      MailDirectoryController(repository);

  DateTime now() => _clock();

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

  /// Files being attached to [draft], and the last files the limits refused.
  List<MailUpload> uploads = const [];
  List<MailFileProblem> fileProblems = const [];

  /// Sender identities of the open mailbox (empty for read access).
  List<MailIdentity> identities = const [];

  /// The person's scheduled messages in the open mailbox.
  MailPhase scheduledPhase = MailPhase.idle;
  String? scheduledError;
  List<MailDraft> scheduledList = const [];

  /// The approval asked for to delete the opened message for good.
  WorkspaceApproval? deleteApproval;

  /// AI on email: whether a model is set up, the opened message's summary and
  /// label, and the shown page's stored labels by UID.
  MailAiStatus? ai;
  MailPhase summaryPhase = MailPhase.idle;
  String? summaryError;
  MailSummary? summary;
  MailLabel? label;
  Map<int, MailLabel> labels = const {};

  /// An AI-drafted reply that waits for approval (medium risk).
  WorkspaceApproval? draftApproval;

  /// The forward asked for from the opened message, with its send approval.
  MailDraft? forwardDraft;

  /// Selected messages of the shown list (by UID), and the bulk action that
  /// waits for an owner's approval: archive, export or delete.
  Set<int> selected = const {};
  String? bulkKind;
  WorkspaceApproval? bulkApproval;

  /// An approved export, downloadable for one hour after approval.
  WorkspaceApproval? exportReady;

  static const bulkLimit = 200, exportLimit = 100;

  /// The action in progress (connect, password, move, send, approve, ...).
  String? busy;

  // One idempotency key per send attempt of a draft (and its send time): a
  // retry after a lost answer reuses it, so the same message is never asked
  // for twice; another time makes a new key and a new approval.
  final _sendKeys = <String, (String, DateTime?)>{};

  bool get hasFilters => query.trim().isNotEmpty || unreadOnly || flaggedOnly;
  List<MailAccount> get accounts => setup?.accounts ?? const [];
  List<MailAccount> get ownAccounts => [
        for (final a in accounts)
          if (a.isOwn) a
      ];
  List<MailAccount> get sharedAccounts => [
        for (final a in accounts)
          if (!a.isOwn) a
      ];

  /// The person may write in the open mailbox (role and share both allow).
  bool get canWrite => (setup?.canAct ?? false) && (account?.canWrite ?? false);

  /// The open mailbox is the person's own (delete for good, shares).
  bool get ownsAccount => (setup?.canAct ?? false) && (account?.isOwn ?? false);
  bool get aiAvailable => ai?.available ?? false;

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
      setup = current.withAccounts(
          [for (final a in current.accounts) a.id == updated.id ? updated : a]);
    }
    if (account?.id == updated.id) account = updated;
  }

  /// Leaves a mailbox someone shared with this person.
  Future<bool> leave(MailAccount target) async {
    final grant = target.grantId;
    if (grant == null) return false;
    var ok = false;
    await _run('leave', () async {
      await repository.removeGrant(grant);
      ok = true;
      if (account?.id == target.id) account = null;
      await loadSetup();
      _say('mc.n.left');
    });
    return ok;
  }

  // -- folders and drafts ----------------------------------------------------

  Future<void> openAccount(MailAccount target) async {
    account = target;
    folders = const [];
    drafts = const [];
    identities = const [];
    scheduledList = const [];
    scheduledPhase = MailPhase.idle;
    labels = const {};
    await Future.wait([loadFolders(), _loadIdentities(), loadAi()]);
  }

  Future<void> _loadIdentities() async {
    final open = account;
    if (open == null || !open.canWrite) return;
    try {
      identities = await repository.identities(open.id);
    } on Object {
      // The composer then offers only the mailbox address.
      identities = const [];
    }
    notifyListeners();
  }

  /// Reads whether AI on email is available (once per mailbox opened).
  Future<void> loadAi() async {
    try {
      ai = await repository.aiStatus();
    } on Object {
      ai = null;
    }
    notifyListeners();
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
    selected = const {};
    bulkApproval = null;
    bulkKind = null;
    exportReady = null;
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
      labels = const {};
      unawaited(_loadLabels(page.items));
    } on Object catch (e) {
      listPhase = phaseFor(e);
      listError = describe(e);
      if (e is ApiException && e.statusCode == 409) await _refreshAccount();
    }
    notifyListeners();
  }

  /// The stored labels of [shown] (no model call). Quietly nothing on error.
  Future<void> _loadLabels(List<MailMessage> shown) async {
    final open = account, inFolder = folder;
    if (open == null || inFolder == null || shown.isEmpty) return;
    try {
      final found = await repository.labels(open.id, inFolder.id, shown);
      labels = {...labels, for (final l in found) l.uid: l};
      notifyListeners();
    } on Object {
      // Labels are a hint; the list is complete without them.
    }
  }

  // -- selecting several messages --------------------------------------------

  bool get selecting => selected.isNotEmpty;
  List<MailMessage> get selectedMessages => [
        for (final m in messages)
          if (selected.contains(m.uid)) m
      ];

  void toggleSelected(MailMessage message) {
    final next = {...selected};
    if (!next.remove(message.uid)) next.add(message.uid);
    selected = next;
    notifyListeners();
  }

  void selectAll() {
    selected = {for (final m in messages) m.uid};
    notifyListeners();
  }

  void clearSelection() {
    selected = const {};
    notifyListeners();
  }

  /// Read, unread, star or remove star on the selection (calls of 200).
  Future<void> flagSelected(String flag, bool value) => _run('bulk', () async {
        final open = account!, inFolder = folder!;
        final uids = selected.toList();
        for (var i = 0; i < uids.length; i += bulkLimit) {
          await repository.setFlags(open.id, inFolder.id,
              uids.sublist(i, (i + bulkLimit).clamp(0, uids.length)),
              flag: flag, value: value);
        }
        messages = [
          for (final m in messages)
            selected.contains(m.uid)
                ? (flag == 'seen'
                    ? m.copyWith(seen: value)
                    : m.copyWith(flagged: value))
                : m
        ];
        _say(l10nCount('mc.b.updated', uids.length));
      });

  /// Moves the selection to a role (archive, junk, trash, inbox).
  Future<void> moveSelected(String role) => _run('bulk', () async {
        final open = account!, inFolder = folder!;
        final uids = selected.toList();
        var moved = 0;
        for (var i = 0; i < uids.length; i += bulkLimit) {
          final part = uids.sublist(i, (i + bulkLimit).clamp(0, uids.length));
          await repository.moveMany(open.id, inFolder.id, part, role);
          moved += part.length;
          final gone = part.toSet();
          messages = [
            for (final m in messages)
              if (!gone.contains(m.uid)) m
          ];
          total = (total - part.length).clamp(0, 1 << 31);
        }
        selected = const {};
        _say(l10nCount('mc.b.moved', moved));
      });

  /// Asks for the approval of archiving, exporting or deleting for good
  /// exactly the selection; nothing happens until an owner approves.
  Future<void> askBulk(String kind) => _run('bulk', () async {
        final open = account!, inFolder = folder!;
        final uids = selected.toList()..sort();
        bulkApproval = switch (kind) {
          'archive' => await repository.archiveMany(open.id, inFolder.id, uids),
          'export' => await repository.exportMany(open.id, inFolder.id, uids),
          _ => await repository.deleteForGood(open.id, inFolder.id, uids),
        };
        bulkKind = kind;
        _say('mc.b.waiting');
      });

  void dismissBulk() {
    bulkApproval = null;
    bulkKind = null;
    notifyListeners();
  }

  /// Approves the bulk action with its hash. Archive and delete take the
  /// messages off the list; an export becomes downloadable for one hour.
  Future<void> approveBulk() async {
    final approval = bulkApproval, kind = bulkKind;
    if (approval == null || kind == null) return;
    await _run('approve', () async {
      final result = await repository.approve(approval);
      final uids = <int>{
        for (final u in (approval.arguments['uids'] as List? ?? const []))
          if (u is int) u
      };
      if (kind == 'export') {
        exportReady = approval;
      } else {
        messages = [
          for (final m in messages)
            if (!uids.contains(m.uid)) m
        ];
        total = (total - uids.length).clamp(0, 1 << 31);
      }
      selected = const {};
      bulkApproval = null;
      bulkKind = null;
      _say(result ?? 'mc.n.approved');
    });
  }

  /// Downloads the approved export (.mbox) and offers to save it.
  Future<void> downloadExport() async {
    final approval = exportReady;
    if (approval == null) return;
    await _run('download', () async {
      final bytes = await transfer.download(
          '/api/v1/mail/ai/exports/${Uri.encodeComponent(approval.id)}');
      final saved = await transfer.save('mail-export.mbox', bytes);
      _say(saved ? 'mc.n.savedFile' : 'mc.n.saveCancelled');
    });
  }

  /// A count notice: the key and the number, said by the screen.
  String l10nCount(String key, int count) => '$key|$count';

  /// Labels the shown page's unlabelled messages (at most 25).
  Future<void> labelPage() async {
    final open = account, inFolder = folder;
    if (open == null || inFolder == null) return;
    final todo = [
      for (final m in messages)
        if (!labels.containsKey(m.uid)) m.uid
    ].take(25).toList();
    if (todo.isEmpty) {
      _say('mc.ai.allLabelled');
      notifyListeners();
      return;
    }
    await _run('label', () async {
      final page = await repository.classifyPage(open.id, inFolder.id, todo);
      labels = {...labels, for (final l in page.labels) l.uid: l};
      _say(page.summary.isEmpty ? 'mc.ai.labelled' : page.summary);
    });
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
      unawaited(_loadLabels(page.items));
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
    deleteApproval = null;
    summary = null;
    summaryError = null;
    summaryPhase = MailPhase.idle;
    draftApproval = null;
    forwardDraft = null;
    label = labels[message.uid];
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

  /// Opens [message] from All inboxes in its own mailbox: the mailbox's
  /// folders, identities and AI, its inbox, then the message, so a reply is a
  /// draft of that mailbox sent from its address after approval.
  Future<void> openFromAllInboxes(
      MailAccount target, MailMessage message) async {
    detail = null;
    detailError = null;
    detailPhase = MailPhase.loading;
    opened = message;
    notifyListeners();
    await openAccount(target);
    folder = folderWithRole('inbox') ??
        const MailFolder(
            id: 'INBOX', name: 'INBOX', role: 'inbox', total: 0, unread: 0);
    messages = [message];
    nextBefore = null;
    await read(message);
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

  /// Delete for good (Trash and Junk only): asks for the approval of exactly
  /// this message; nothing is deleted until an owner approves.
  Future<void> deleteForGood(MailMessage message) =>
      _run('deleteForGood', () async {
        deleteApproval = await repository
            .deleteForGood(account!.id, message.folder, [message.uid]);
        _say('mc.n.deleteAsked');
      });

  /// Approves the delete with its hash; the message then leaves the list.
  Future<bool> approveDelete() async {
    final approval = deleteApproval, message = detail?.message;
    if (approval == null || message == null) return false;
    var ok = false;
    await _run('approve', () async {
      final result = await repository.approve(approval);
      ok = true;
      deleteApproval = null;
      messages = [
        for (final m in messages)
          if (!(m.uid == message.uid && m.folder == message.folder)) m
      ];
      total = total > 0 ? total - 1 : 0;
      _say(result ?? 'mc.n.deleted');
    });
    return ok;
  }

  // -- received attachments ------------------------------------------------------

  /// Downloads a received attachment and offers to save it.
  Future<void> downloadReceived(MailAttachment file) async {
    final open = account, message = detail?.message;
    if (open == null || message == null) return;
    await _run('download', () async {
      final path = Uri(
          path: '/api/v1/mail/accounts/${Uri.encodeComponent(open.id)}'
              '/attachment',
          queryParameters: {
            'folder': message.folder,
            'uid': '${message.uid}',
            'index': '${file.index}',
          }).toString();
      final bytes = await transfer.download(path);
      final saved = await transfer.save(file.saveName, bytes);
      _say(saved ? 'mc.n.savedFile' : 'mc.n.saveCancelled');
    });
  }

  // -- AI on one message --------------------------------------------------------

  Future<void> summarize({bool thread = false}) async {
    final open = account, message = detail?.message;
    if (open == null || message == null) return;
    summaryPhase = MailPhase.loading;
    summaryError = null;
    notifyListeners();
    try {
      summary = await repository.summarize(open.id, message.folder, message.uid,
          thread: thread);
      summaryPhase = MailPhase.ready;
    } on Object catch (e) {
      summaryPhase = phaseFor(e);
      summaryError = describe(e);
    }
    notifyListeners();
  }

  Future<void> classifyOpened() async {
    final open = account, message = detail?.message;
    if (open == null || message == null) return;
    await _run('label', () async {
      label = await repository.classify(open.id, message.folder, message.uid);
      labels = {...labels, message.uid: label!};
    });
  }

  /// Drafts a reply with the model; true when the draft is ready to edit.
  /// When the workspace asks approval for drafting, [draftApproval] is set.
  Future<bool> draftReplyWithAi(
      {String kind = 'reply', String? instructions}) async {
    final open = account, message = detail?.message;
    if (open == null || message == null) return false;
    var ok = false;
    await _run('aiDraft', () async {
      final result = await repository.draftReply(
          open.id, message.folder, message.uid,
          kind: kind, instructions: instructions);
      final written = result.draft;
      if (written != null) {
        draft = written;
        _upsertDraft(written);
        ok = true;
        _say(result.injectionSuspected ? 'mc.ai.injection' : 'mc.ai.drafted');
      } else {
        draftApproval = result.approval;
        _say('mc.ai.draftWaiting');
      }
    });
    return ok;
  }

  final _forwardKeys = <String, String>{};

  /// Forwards the opened message to [to] through the send approval; the same
  /// inputs reuse one key, so a retry never asks twice.
  Future<void> forwardWithAi(List<String> to,
      {String? note, bool writeNote = false}) async {
    final open = account, message = detail?.message;
    if (open == null || message == null) return;
    await _run('forward', () async {
      final sig = '${open.id}|${message.folder}|${message.uid}|'
          '${to.join(',')}|${note ?? ''}|$writeNote';
      final key = _forwardKeys[sig] ??=
          'mobile-fwd:${message.uid}:${_clock().microsecondsSinceEpoch}';
      forwardDraft = await repository.forward(
          open.id, message.folder, message.uid,
          to: to, note: note, writeNote: writeNote, key: key);
      _upsertDraft(forwardDraft!);
      _say('mc.n.waiting');
    });
  }

  Future<void> approveForward() async {
    final approval = forwardDraft?.approval;
    if (approval == null) return;
    await _run('approve', () async {
      final result = await repository.approve(approval);
      final open = account;
      if (open != null) {
        drafts = await repository.drafts(open.id);
        forwardDraft =
            drafts.where((d) => d.id == forwardDraft!.id).firstOrNull ??
                forwardDraft;
      }
      _say(result ?? 'mc.n.approved');
    });
  }

  /// Approves the AI draft's approval with its hash, then opens the drafts.
  Future<void> approveAiDraft() async {
    final approval = draftApproval;
    if (approval == null) return;
    await _run('approve', () async {
      final result = await repository.approve(approval);
      draftApproval = null;
      final open = account;
      if (open != null) drafts = await repository.drafts(open.id);
      _say(result ?? 'mc.ai.drafted');
    });
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
    fileProblems = const [];
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

  /// Saves [edit], then asks to send, now or at [sendAt]: an owner approves
  /// the exact message (and the time).
  Future<void> askToSend(MailDraftEdit edit, {DateTime? sendAt}) async {
    final current = draft;
    if (current == null) return;
    await _run(sendAt == null ? 'send' : 'schedule', () async {
      var latest = current;
      if (_changed(current, edit)) {
        latest = await repository.updateDraft(current.id, edit);
        _sendKeys.remove(current.id);
      }
      final known = _sendKeys[current.id];
      final key = known != null && known.$2 == sendAt
          ? known.$1
          : 'mobile:${current.id}:${_clock().microsecondsSinceEpoch}';
      _sendKeys[current.id] = (key, sendAt);
      draft = await repository.askToSend(latest.id, key, sendAt: sendAt);
      _upsertDraft(draft!);
      _say(sendAt != null
          ? 'mc.n.scheduleAsked'
          : (draft!.waiting ? 'mc.n.waiting' : 'mc.n.asked'));
    });
  }

  /// The problem with [sendAt] before anything is asked, or null.
  String? scheduleProblem(DateTime sendAt) {
    final now = _clock();
    if (sendAt.isBefore(now.add(const Duration(minutes: 1)))) {
      return 'mc.s.tooSoon';
    }
    final days = setup?.scheduleMaxDays ?? 30;
    if (sendAt.isAfter(now.add(Duration(days: days)))) return 'mc.s.tooLate';
    return null;
  }

  /// Turns a scheduled message back into a draft.
  Future<void> cancelSchedule(MailDraft target) =>
      _run('cancelSchedule', () async {
        final updated = await repository.cancelSchedule(target.id);
        _sendKeys.remove(target.id);
        _upsertDraft(updated);
        if (draft?.id == target.id) draft = updated;
        scheduledList = [
          for (final d in scheduledList)
            if (d.id != target.id) d
        ];
        _say('mc.n.scheduleCancelled');
      });

  Future<void> loadScheduled() async {
    final open = account;
    if (open == null) return;
    scheduledPhase = MailPhase.loading;
    scheduledError = null;
    notifyListeners();
    try {
      scheduledList = await repository.scheduled(open.id);
      scheduledPhase = MailPhase.ready;
    } on Object catch (e) {
      scheduledPhase = phaseFor(e);
      scheduledError = describe(e);
    }
    notifyListeners();
  }

  /// Picks another sender: saves [edit] with it (the server swaps the
  /// signature in the text); true when the draft came back.
  Future<bool> setIdentity(MailDraftEdit edit, String? identityId) async {
    final current = draft;
    if (current == null) return false;
    var ok = false;
    await _run('identity', () async {
      draft = await repository.updateDraft(
          current.id,
          MailDraftEdit(
              to: edit.to,
              cc: edit.cc,
              bcc: edit.bcc,
              subject: edit.subject,
              body: edit.body,
              identityId: identityId,
              setIdentity: true));
      _sendKeys.remove(current.id);
      _upsertDraft(draft!);
      ok = true;
    });
    return ok;
  }

  // -- attachments on the draft ---------------------------------------------------

  /// Picks device files and attaches those the limits allow, one at a time
  /// with progress; the rest are listed in [fileProblems].
  Future<void> attachFiles() async {
    final current = draft, limits = setup;
    if (current == null || limits == null) return;
    List<PickedMailFile> picked;
    try {
      picked = await transfer.pick(limits.attachmentExtensions);
    } on Object catch (e) {
      _say(describe(e), error: true);
      notifyListeners();
      return;
    }
    if (picked.isEmpty) return;
    final (ok, problems) = fileProblemsFor(picked, current, limits);
    fileProblems = problems;
    notifyListeners();
    for (final file in ok) {
      final upload = MailUpload(file.name, file.size);
      uploads = [...uploads, upload];
      notifyListeners();
      try {
        await transfer.upload(current.id, file, onProgress: (sent, total) {
          upload.sent = sent;
          notifyListeners();
        }, cancellation: upload.cancellation);
        _sendKeys.remove(current.id);
      } on UploadCancelledException {
        _say('mc.f.stopped');
      } on Object catch (e) {
        _say(describe(e), error: true);
      }
      uploads = [
        for (final u in uploads)
          if (u != upload) u
      ];
      notifyListeners();
    }
    await _refreshDraft();
  }

  void stopUpload(MailUpload upload) => upload.cancellation.cancel();

  Future<void> removeFile(MailOutgoingFile file) async {
    final current = draft;
    if (current == null) return;
    await _run('removeFile', () async {
      await repository.removeAttachment(current.id, file.id);
      _sendKeys.remove(current.id);
      await _refreshDraft();
      _say('mc.f.removed');
    });
  }

  Future<void> downloadFile(MailOutgoingFile file) async {
    final current = draft;
    if (current == null) return;
    await _run('download', () async {
      final bytes = await transfer
          .download('/api/v1/mail/drafts/${Uri.encodeComponent(current.id)}'
              '/attachments/${Uri.encodeComponent(file.id)}');
      final saved = await transfer.save(file.filename, bytes);
      _say(saved ? 'mc.n.savedFile' : 'mc.n.saveCancelled');
    });
  }

  void clearFileProblems() {
    fileProblems = const [];
    notifyListeners();
  }

  Future<void> _refreshDraft() async {
    final current = draft;
    if (current == null) return;
    try {
      draft = await repository.getDraft(current.id);
      _upsertDraft(draft!);
    } on Object catch (e) {
      _say(describe(e), error: true);
    }
    notifyListeners();
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
    deleteApproval = draftApproval = bulkApproval = exportReady = null;
    forwardDraft = null;
    bulkKind = null;
    selected = const {};
    ai = null;
    summary = null;
    label = null;
    summaryPhase = scheduledPhase = MailPhase.idle;
    summaryError = scheduledError = null;
    labels = const {};
    identities = const [];
    scheduledList = const [];
    uploads = const [];
    fileProblems = const [];
    folders = const [];
    drafts = const [];
    messages = const [];
    total = 0;
    nextBefore = null;
    query = '';
    unreadOnly = flaggedOnly = false;
    _sendKeys.clear();
    directory.reset();
    notifyListeners();
  }

  @override
  void dispose() {
    directory.dispose();
    super.dispose();
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

  void _say(String text, {bool error = false}) => say(text, error: error);
}
