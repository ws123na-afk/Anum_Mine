import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../workspace/workspace_models.dart';
import 'whatsapp_models.dart';
import 'whatsapp_repository.dart';

enum WaPhase { initial, loading, ready, notConnected, denied, error }

/// The WhatsApp inbox list (docs/whatsapp.md): the account, the conversations
/// with filter, search and paging, and the caller's role (owners approve).
/// Mirrors the web `WhatsAppView`.
class WhatsAppController extends ChangeNotifier {
  WhatsAppController(this.repository);
  final WhatsAppRepository repository;

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
      {required this.owner, this.onChanged});
  final WhatsAppRepository repository;
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
    busy = 'send';
    notice = noticeKey = null;
    _notify();
    var ok = false;
    try {
      final result = await repository.sendText(conversationId, value);
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

/// Whether [approval] is still pending and unexpired at [now].
bool approvable(WorkspaceApproval approval, DateTime now) =>
    approval.status == 'pending' &&
    approval.payloadHash != null &&
    (approval.expiresAt == null || approval.expiresAt!.isAfter(now));
