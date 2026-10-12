import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'email_models.dart';

/// The email summary's tabs (docs/email.md): every message, urgent (high
/// priority), replied (a reply was sent from ANUM) and the rest.
enum SummaryTab { all, urgent, replied, other }

extension SummaryTabQuery on SummaryTab {
  Map<String, String> get params => switch (this) {
        SummaryTab.all => const {},
        SummaryTab.urgent => const {'urgent': 'true'},
        SummaryTab.replied => const {'replied': 'true'},
        SummaryTab.other => const {'urgent': 'false', 'replied': 'false'},
      };

  static SummaryTab parse(String? value) => SummaryTab.values
      .firstWhere((t) => t.name == value, orElse: () => SummaryTab.all);
}

class InboxCounts {
  const InboxCounts(
      {this.accounts = 0, this.unread = 0, this.urgentUnread = 0});
  final int accounts, unread, urgentUnread;

  factory InboxCounts.fromJson(JsonReader j) => InboxCounts(
        accounts: j.optInt('accounts') ?? 0,
        unread: j.optInt('unread') ?? 0,
        urgentUnread: j.optInt('high_priority_unread') ?? 0,
      );
}

abstract interface class EmailSummaryRepository {
  Future<InboxCounts> counts();
  Future<EmailPage> messages(SummaryTab tab, {String? before});
}

class ApiEmailSummaryRepository implements EmailSummaryRepository {
  const ApiEmailSummaryRepository(this.api);
  final AnumApiClient api;

  @override
  Future<InboxCounts> counts() async => InboxCounts.fromJson(
      JsonReader(await api.request('GET', '/api/v1/email/summary')));

  @override
  Future<EmailPage> messages(SummaryTab tab, {String? before}) async {
    final value = JsonReader(await api.request(
      'GET',
      Uri(path: '/api/v1/email/messages', queryParameters: {
        'limit': '25',
        ...tab.params,
        if (before != null) 'before': before,
      }).toString(),
    ));
    return EmailPage(value.objects('items', EmailMessage.fromJson),
        value.optString('next_cursor'));
  }
}

enum SummaryPhase { idle, loading, ready, error, denied }

class EmailSummaryController extends ChangeNotifier {
  EmailSummaryController(this.repository, {this.tab = SummaryTab.all});
  final EmailSummaryRepository repository;

  SummaryTab tab;
  SummaryPhase phase = SummaryPhase.idle;
  String? error;
  InboxCounts counts = const InboxCounts();
  List<EmailMessage> messages = const [];
  String? nextCursor;
  bool loadingMore = false;
  String? notice;

  Future<void> load() async {
    phase = SummaryPhase.loading;
    error = null;
    notifyListeners();
    try {
      counts = await repository.counts();
      final page = await repository.messages(tab);
      messages = page.items;
      nextCursor = page.nextCursor;
      phase = SummaryPhase.ready;
    } on ApiException catch (e) {
      phase = e.statusCode == 403 ? SummaryPhase.denied : SummaryPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = SummaryPhase.error;
      error = '$e';
    }
    notifyListeners();
  }

  Future<void> select(SummaryTab value) async {
    if (value == tab && phase == SummaryPhase.ready) return;
    tab = value;
    await load();
  }

  Future<void> loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.messages(tab, before: cursor);
      messages = [...messages, ...page.items];
      nextCursor = page.nextCursor;
    } on Object catch (e) {
      notice = e is ApiException ? e.readable : '$e';
    }
    loadingMore = false;
    notifyListeners();
  }
}
