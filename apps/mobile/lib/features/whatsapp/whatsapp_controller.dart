import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../business/support_controller.dart';
import '../workspace/workspace_models.dart';
import 'whatsapp_models.dart';
import 'whatsapp_repository.dart';

enum WaPhase { initial, loading, ready, notConnected, denied, error }

/// The WhatsApp inbox list (docs/whatsapp.md): the account, the conversations
/// with filter, search and paging, and the caller's role (owners approve).
/// Mirrors the web `WhatsAppView`.
class WhatsAppController extends ChangeNotifier {
  WhatsAppController(this.repository, {this.support, this.apiBase});
  final WhatsAppRepository repository;

  /// The API's public address, for the webhook URL shown on Setup.
  final Uri? apiBase;

  /// Business support tickets, when wired: "Create ticket" on a conversation
  /// (docs/business.md#tickets-from-an-inbox). Null hides the action.
  final SupportController? support;

  static const pageSize = 30;

  WaPhase phase = WaPhase.initial;
  WaAccount? account;
  List<WaConversation> items = const [];
  bool hasMore = false;
  bool loadingMore = false;
  WaFilter filter = WaFilter.all;
  String query = '';
  String? error;

  /// True for an active owner (may approve sends); null until known.
  bool? owner;
  bool _disposed = false;
  int _token = 0;

  Future<void> load() async {
    final token = ++_token;
    if (phase != WaPhase.ready) phase = WaPhase.loading;
    error = null;
    _notify();
    try {
      final found = await repository.account();
      if (token != _token || _disposed) return;
      account = found;
      if (!found.configured) {
        phase = WaPhase.notConnected;
        items = const [];
        _notify();
        return;
      }
      owner ??= await _owner();
      final page = await repository.conversations(
          limit: pageSize, filter: filter, query: query);
      if (token != _token || _disposed) return;
      items = page;
      hasMore = page.length == pageSize;
      phase = WaPhase.ready;
    } on ApiException catch (e) {
      if (token != _token) return;
      phase = e.isPermissionDenied ? WaPhase.denied : WaPhase.error;
      error = e.message;
    } on Object catch (e) {
      if (token != _token) return;
      phase = WaPhase.error;
      error = '$e';
    }
    _notify();
  }

  Future<bool> _owner() async {
    try {
      return await repository.currentRole() == 'owner';
    } on Object {
      return false;
    }
  }

  Future<void> setFilter(WaFilter value) async {
    if (value == filter) return;
    filter = value;
    items = const [];
    phase = WaPhase.loading;
    await load();
  }

  Future<void> search(String value) async {
    query = value.trim();
    items = const [];
    phase = WaPhase.loading;
    await load();
  }

  Future<void> loadMore() async {
    if (!hasMore || loadingMore || items.isEmpty) return;
    loadingMore = true;
    _notify();
    try {
      final page = await repository.conversations(
          limit: pageSize, before: items.last.id, filter: filter, query: query);
      final known = items.map((item) => item.id).toSet();
      items = [...items, ...page.where((item) => !known.contains(item.id))];
      hasMore = page.length == pageSize;
    } on Object catch (e) {
      error = e is ApiException ? e.message : '$e';
    }
    loadingMore = false;
    _notify();
  }

  /// Keep the list in step with what a thread learned (read, new message).
  void replace(WaConversation conversation) {
    items = [
      for (final item in items) item.id == conversation.id ? conversation : item
    ];
    _notify();
  }

  void reset() {
    _token++;
    phase = WaPhase.initial;
    account = null;
    items = const [];
    owner = null;
    filter = WaFilter.all;
    query = '';
    _notify();
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

/// One conversation: its messages (paging back), the suggested reply, a
/// reply sent for approval, and approve or reject for owners.
class WhatsAppThreadController extends ChangeNotifier {
  WhatsAppThreadController(this.repository, this.conversationId,
      {required this.owner, this.onChanged, this.support});
  final WhatsAppRepository repository;

  /// Business support tickets, when wired (see [WhatsAppController.support]).
  final SupportController? support;
  final String conversationId;
  final bool owner;
  final void Function(WaConversation)? onChanged;

  WaConversation? conversation;
  List<WaMessage> messages = const [];
  bool hasMore = false;
  bool loading = true;
  String? error;

  /// `draft`, `send`, `older`, `approve:<id>` or `reject:<id>`.
  String? busy;

  /// A translated outcome (a catalog key) or, when null, [notice] as the API wrote it.
  String? noticeKey;
  String? notice;
  bool noticeFailed = false;
  WaSuggestion? suggestion;
  bool budgetExceeded = false;
  bool _disposed = false;

  Future<void> load({bool first = false}) async {
    try {
      final page = await repository.thread(conversationId,
          limit: WhatsAppController.pageSize);
      if (_disposed) return;
      conversation = page.conversation;
      suggestion ??= page.conversation.suggestion;
      messages = first ? page.messages : mergeMessages(messages, page.messages);
      if (first) hasMore = page.hasMore;
      error = null;
      if (first && page.conversation.unread > 0) {
        try {
          final read = await repository.markRead(conversationId);
          conversation = read;
        } on Object {
          // Unread stays; nothing else depends on it.
        }
      }
      onChanged?.call(conversation!);
    } on Object catch (e) {
      error = e is ApiException ? e.message : '$e';
    }
    loading = false;
    _notify();
  }

  Future<void> older() async {
    if (messages.isEmpty) return;
    busy = 'older';
    _notify();
    try {
      final page = await repository.thread(conversationId,
          limit: WhatsAppController.pageSize, before: messages.first.id);
      messages = mergeMessages(messages, page.messages);
      hasMore = page.hasMore;
    } on Object catch (e) {
      _fail(e);
    }
    busy = null;
    _notify();
  }

  Future<void> draft() async {
    busy = 'draft';
    notice = noticeKey = null;
    budgetExceeded = false;
    _notify();
    try {
      suggestion = await repository.draft(conversationId);
    } on ApiException catch (e) {
      budgetExceeded = e.isBudgetExceeded;
      _fail(e);
    } on Object catch (e) {
      _fail(e);
    }
    busy = null;
    _notify();
  }

  /// Ask to send [text]; it waits for an owner's approval. True when queued.
  Future<bool> send(String text) async {
    final value = text.trim();
    if (value.isEmpty) return false;
    return _deliver(() => repository.sendText(conversationId, value));
  }

  /// Send an approved [template] with its [parameters]. Whether it goes out
  /// at once or waits for an owner is the server's decision (the auto-send
  /// list, autonomy and approval rules); the outcome is shown either way.
  /// True unless it failed.
  Future<bool> sendTemplate(WaTemplate template, List<String> parameters) =>
      _deliver(
          () => repository.sendTemplate(conversationId, template, parameters));

  /// The bytes of [message]'s attachment, or null after saying why not (the
  /// server's sentence for 404, 413 and 502).
  Future<Uint8List?> fetchMedia(WaMessage message) async {
    busy = 'media:${message.id}';
    notice = noticeKey = null;
    _notify();
    Uint8List? bytes;
    try {
      bytes = await repository.mediaBytes(message.id);
    } on Object catch (e) {
      _fail(e);
    }
    busy = null;
    _notify();
    return bytes;
  }

  /// Hand [bytes] to the platform's save dialog. True when saved.
  Future<bool> saveMedia(WaMessage message, Uint8List bytes) async {
    busy = 'media:${message.id}';
    notice = noticeKey = null;
    _notify();
    var saved = false;
    try {
      saved = await repository.saveMedia(mediaFileName(message), bytes);
      if (saved) {
        noticeKey = 'waOps.mediaSaved';
        noticeFailed = false;
      }
    } on Object catch (e) {
      _fail(e);
    }
    busy = null;
    _notify();
    return saved;
  }

  Future<bool> _deliver(Future<WaSendResult> Function() request) async {
    busy = 'send';
    notice = noticeKey = null;
    _notify();
    var ok = false;
    try {
      final result = await request();
      messages = mergeMessages(messages, [result.message]);
      noticeKey = result.awaitingApproval
          ? (owner ? 'waAwaitingOwner' : 'waAwaitingMember')
          : result.outcome == 'sent'
              ? 'waSent'
              : null;
      notice = noticeKey == null ? result.detail : null;
      noticeFailed = result.outcome == 'failed';
      ok = result.outcome != 'failed';
    } on Object catch (e) {
      _fail(e);
    }
    busy = null;
    _notify();
    return ok;
  }

  Future<void> decide(WaMessage message, {required bool approve}) async {
    final approval = message.approval;
    if (approval == null) return;
    busy = '${approve ? 'approve' : 'reject'}:${message.id}';
    notice = noticeKey = null;
    _notify();
    try {
      if (approve) {
        final result = await repository.approve(approval);
        noticeKey = 'waApproved';
        notice = result;
      } else {
        await repository.reject(approval);
        noticeKey = 'waRejected';
      }
      noticeFailed = false;
      await load();
    } on Object catch (e) {
      _fail(e);
    }
    busy = null;
    _notify();
  }

  void _fail(Object e) {
    noticeKey = null;
    notice = e is ApiException ? e.message : '$e';
    noticeFailed = true;
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

/// A file name for saving [message]'s attachment: the sender's name, else the
/// message kind with an extension from its type.
String mediaFileName(WaMessage message) {
  final named = message.media?.filename?.trim() ?? '';
  if (named.isNotEmpty) return named;
  final extension = switch ((message.media?.mimeType ?? '').toLowerCase()) {
    'image/jpeg' => '.jpg',
    'image/png' => '.png',
    'image/webp' => '.webp',
    'image/gif' => '.gif',
    'application/pdf' => '.pdf',
    'audio/ogg' => '.ogg',
    'audio/mpeg' => '.mp3',
    'audio/mp4' => '.m4a',
    'video/mp4' => '.mp4',
    _ => '',
  };
  return '${message.kind}-${message.id}$extension';
}

/// Whether [approval] is still pending and unexpired at [now].
bool approvable(WorkspaceApproval approval, DateTime now) =>
    approval.status == 'pending' &&
    approval.payloadHash != null &&
    (approval.expiresAt == null || approval.expiresAt!.isAfter(now));
