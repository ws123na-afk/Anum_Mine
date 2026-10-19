import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'mail_center_controller.dart';
import 'mail_center_models.dart';
import 'mail_center_screens.dart';

/// All inboxes (docs/email.md#all-inboxes): one list, newest first, over the
/// inbox of every mailbox the person may read (their own and those shared
/// with them; the API decides). Each row names its mailbox; a message opens
/// in its own mailbox, so a reply is sent from that address after approval.

class MailAllInboxMessage {
  const MailAllInboxMessage({
    required this.message,
    required this.accountId,
    required this.accountAddress,
    required this.access,
    this.accountName,
  });

  factory MailAllInboxMessage.fromJson(JsonReader j) => MailAllInboxMessage(
        message: MailMessage.fromJson(j),
        accountId: j.string('account_id'),
        accountAddress: j.string('account_address'),
        accountName: j.optString('account_name'),
        access: j.optString('access') ?? 'owner',
      );

  final MailMessage message;
  final String accountId, accountAddress, access;
  final String? accountName;

  /// The same UID can exist in two inboxes.
  String get key => '$accountId:${message.uid}';

  /// The mailbox badge: its name when it has one, else the address.
  String get mailbox {
    final name = accountName?.trim() ?? '';
    return name.isEmpty ? accountAddress : name;
  }

  MailAllInboxMessage copyWith({bool? seen, bool? flagged}) =>
      MailAllInboxMessage(
        message: message.copyWith(seen: seen, flagged: flagged),
        accountId: accountId,
        accountAddress: accountAddress,
        accountName: accountName,
        access: access,
      );
}

class MailAllInboxMailbox {
  const MailAllInboxMailbox(
      this.accountId, this.address, this.state, this.error);

  factory MailAllInboxMailbox.fromJson(JsonReader j) => MailAllInboxMailbox(
        j.string('account_id'),
        j.string('address'),
        j.optString('state') ?? 'ok',
        j.optString('error'),
      );

  final String accountId, address, state;
  final String? error;

  /// Not read for this page (offline, refused password, error).
  bool get unreadable => state != 'ok' && state != 'done';
}

class MailAllInboxPage {
  const MailAllInboxPage(
      this.items, this.total, this.nextCursor, this.mailboxes);

  factory MailAllInboxPage.fromJson(JsonReader j) => MailAllInboxPage(
        j.objects('items', MailAllInboxMessage.fromJson),
        j.optInt('total') ?? 0,
        j.optString('next_cursor'),
        j.optObjects('mailboxes', MailAllInboxMailbox.fromJson),
      );

  final List<MailAllInboxMessage> items;
  final int total;
  final String? nextCursor;
  final List<MailAllInboxMailbox> mailboxes;
}

/// Search, Unread, Starred, one mailbox (null for all) and a received-date
/// range (inclusive days).
class MailAllInboxFilters {
  const MailAllInboxFilters({
    this.query = '',
    this.unread = false,
    this.flagged = false,
    this.accountId,
    this.since,
    this.until,
  });

  final String query;
  final bool unread, flagged;
  final String? accountId;
  final DateTime? since, until;

  bool get active =>
      query.trim().isNotEmpty ||
      unread ||
      flagged ||
      accountId != null ||
      since != null ||
      until != null;

  MailAllInboxFilters copyWith({
    String? query,
    bool? unread,
    bool? flagged,
    String? Function()? accountId,
    DateTime? Function()? since,
    DateTime? Function()? until,
  }) =>
      MailAllInboxFilters(
        query: query ?? this.query,
        unread: unread ?? this.unread,
        flagged: flagged ?? this.flagged,
        accountId: accountId != null ? accountId() : this.accountId,
        since: since != null ? since() : this.since,
        until: until != null ? until() : this.until,
      );

  static String day(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-${value.month.toString().padLeft(2, '0')}-${value.day.toString().padLeft(2, '0')}';

  /// The query the API takes: only what is set.
  Map<String, String> toQuery({String? cursor, int limit = 25}) => {
        'limit': '$limit',
        if (query.trim().isNotEmpty) 'q': query.trim(),
        if (unread) 'unread': 'true',
        if (flagged) 'flagged': 'true',
        if (accountId != null) 'account_id': accountId!,
        if (since != null) 'since': day(since!),
        if (until != null) 'until': day(until!),
        if (cursor != null) 'cursor': cursor,
      };
}

abstract interface class MailAllInboxesRepository {
  Future<MailAllInboxPage> page(MailAllInboxFilters filters, {String? cursor});
  Future<void> setFlag(String accountId, int uid,
      {required String flag, required bool value});
}

class ApiMailAllInboxesRepository implements MailAllInboxesRepository {
  const ApiMailAllInboxesRepository(this.api);
  final AnumApiClient api;

  @override
  Future<MailAllInboxPage> page(MailAllInboxFilters filters,
          {String? cursor}) async =>
      MailAllInboxPage.fromJson(JsonReader(await api.request(
        'GET',
        Uri(
          path: '/api/v1/mail/all-inboxes',
          queryParameters: filters.toQuery(cursor: cursor),
        ).toString(),
      )));

  @override
  Future<void> setFlag(String accountId, int uid,
          {required String flag, required bool value}) async =>
      api.request(
        'POST',
        '/api/v1/mail/accounts/${Uri.encodeComponent(accountId)}/flags',
        body: {
          'folder': 'INBOX',
          'uids': [uid],
          'flag': flag,
          'value': value,
        },
      );
}

class MailAllInboxesController extends ChangeNotifier {
  MailAllInboxesController(this.repository);
  final MailAllInboxesRepository repository;

  MailPhase phase = MailPhase.idle;
  String? error;
  MailAllInboxFilters filters = const MailAllInboxFilters();
  List<MailAllInboxMessage> items = const [];
  List<MailAllInboxMailbox> unreadable = const [];
  int total = 0;
  String? nextCursor;
  bool loadingMore = false;
  String? moreError;
  int _generation = 0;

  Future<void> load() async {
    final generation = ++_generation;
    phase = MailPhase.loading;
    error = null;
    moreError = null;
    notifyListeners();
    try {
      final page = await repository.page(filters);
      if (generation != _generation) return;
      items = page.items;
      total = page.total;
      nextCursor = page.nextCursor;
      unreadable = page.mailboxes.where((m) => m.unreadable).toList();
      phase = MailPhase.ready;
    } on Object catch (e) {
      if (generation != _generation) return;
      phase = phaseFor(e);
      error = describe(e);
    }
    notifyListeners();
  }

  Future<void> loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    final generation = _generation;
    loadingMore = true;
    moreError = null;
    notifyListeners();
    try {
      final page = await repository.page(filters, cursor: cursor);
      if (generation != _generation) return;
      final seen = {for (final m in items) m.key};
      items = [
        ...items,
        for (final m in page.items)
          if (!seen.contains(m.key)) m
      ];
      nextCursor = page.nextCursor;
      unreadable = page.mailboxes.where((m) => m.unreadable).toList();
    } on Object catch (e) {
      moreError = describe(e);
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  Future<void> apply(MailAllInboxFilters next) {
    filters = next;
    return load();
  }

  Future<String?> toggleStar(MailAllInboxMessage item) async {
    final value = !item.message.flagged;
    try {
      await repository.setFlag(item.accountId, item.message.uid,
          flag: 'flagged', value: value);
      _replace(item.copyWith(flagged: value));
      return null;
    } on Object catch (e) {
      return describe(e);
    }
  }

  void markSeen(MailAllInboxMessage item) =>
      _replace(item.copyWith(seen: true));

  void _replace(MailAllInboxMessage next) {
    items = [for (final m in items) m.key == next.key ? next : m];
    notifyListeners();
  }
}

class MailAllInboxesScreen extends StatefulWidget {
  const MailAllInboxesScreen({
    super.key,
    required this.controller,
    required this.mail,
  });
  final MailAllInboxesController controller;
  final MailCenterController mail;

  @override
  State<MailAllInboxesScreen> createState() => _MailAllInboxesScreenState();
}

class _MailAllInboxesScreenState extends State<MailAllInboxesScreen> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    _search.text = widget.controller.filters.query;
    Future.microtask(widget.controller.load);
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  MailAccount? _account(String id) =>
      widget.mail.accounts.where((a) => a.id == id).firstOrNull;

  Future<void> _open(MailAllInboxMessage item) async {
    final account = _account(item.accountId);
    final l = AnumLocalizations.maybeOf(context);
    if (account == null) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l.t('mc.all.gone'))));
      return;
    }
    if (account.canWrite && (widget.mail.setup?.canAct ?? false)) {
      widget.controller.markSeen(item);
    }
    final opening = widget.mail.openFromAllInboxes(account, item.message);
    await Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) => MailMessageScreen(controller: widget.mail)),
    );
    await opening;
  }

  Future<void> _pickDates() async {
    final c = widget.controller;
    final now = DateTime.now();
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(now.year - 10),
      lastDate: DateTime(now.year, now.month, now.day),
      initialDateRange: c.filters.since != null && c.filters.until != null
          ? DateTimeRange(start: c.filters.since!, end: c.filters.until!)
          : null,
    );
    if (picked == null) return;
    await c.apply(
        c.filters.copyWith(since: () => picked.start, until: () => picked.end));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('mc.all.title')),
        actions: [
          IconButton(
            onPressed: c.phase == MailPhase.loading ? null : c.load,
            tooltip: l.t('refresh'),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) => mailPage(_body(context, l, c)),
      ),
    );
  }

  Widget _filters(
      BuildContext context, AnumLocalizations l, MailAllInboxesController c) {
    final f = c.filters;
    final m = MaterialLocalizations.of(context);
    final accounts = widget.mail.accounts;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _search,
          textInputAction: TextInputAction.search,
          maxLength: 200,
          decoration: InputDecoration(
            labelText: l.t('mc.all.search'),
            counterText: '',
            prefixIcon: const Icon(Icons.search),
          ),
          onSubmitted: (value) => c.apply(f.copyWith(query: value)),
        ),
        const SizedBox(height: AnumSpacing.xs),
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            FilterChip(
              label: Text(l.t('mc.all.unread')),
              selected: f.unread,
              onSelected: (v) => c.apply(f.copyWith(unread: v)),
            ),
            FilterChip(
              label: Text(l.t('mc.all.starred')),
              selected: f.flagged,
              onSelected: (v) => c.apply(f.copyWith(flagged: v)),
            ),
            ActionChip(
              avatar: const Icon(Icons.date_range, size: 18),
              label: Text(f.since == null || f.until == null
                  ? l.t('mc.all.dates')
                  : '${m.formatShortDate(f.since!)} - ${m.formatShortDate(f.until!)}'),
              onPressed: _pickDates,
            ),
            if (f.since != null)
              IconButton(
                tooltip: l.t('mc.all.clearDates'),
                onPressed: () =>
                    c.apply(f.copyWith(since: () => null, until: () => null)),
                icon: const Icon(Icons.close),
              ),
          ],
        ),
        const SizedBox(height: AnumSpacing.xs),
        DropdownButtonFormField<String?>(
          key: ValueKey(f.accountId),
          initialValue: f.accountId,
          isExpanded: true,
          decoration: InputDecoration(labelText: l.t('mc.all.mailbox')),
          items: [
            DropdownMenuItem<String?>(
                value: null, child: Text(l.t('mc.all.everyMailbox'))),
            for (final a in accounts)
              DropdownMenuItem<String?>(
                value: a.id,
                child: Text(
                  a.isOwn
                      ? a.address
                      : l.f('mc.all.sharedOption', {'address': a.address}),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
          onChanged: (value) => c.apply(f.copyWith(accountId: () => value)),
        ),
        if (f.active)
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: TextButton(
              onPressed: () {
                _search.clear();
                c.apply(const MailAllInboxFilters());
              },
              child: Text(l.t('mc.all.clear')),
            ),
          ),
      ],
    );
  }

  Widget _body(
      BuildContext context, AnumLocalizations l, MailAllInboxesController c) {
    if (c.phase == MailPhase.denied) {
      return MailState(icon: Icons.lock_outline, title: l.t('mc.denied'));
    }
    final children = <Widget>[
      _filters(context, l, c),
      for (final problem in c.unreadable)
        Card(
          color: Theme.of(context).colorScheme.errorContainer,
          child: Padding(
            padding: const EdgeInsets.all(AnumSpacing.sm),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.cloud_off_outlined),
                    const SizedBox(width: AnumSpacing.xs),
                    Expanded(
                      child: Text(l.f('mc.all.unreadable', {
                        'address': problem.address,
                        'reason': problem.error ?? ''
                      })),
                    ),
                  ],
                ),
                Align(
                  alignment: AlignmentDirectional.centerEnd,
                  child: TextButton(
                      onPressed: c.load, child: Text(l.t('mc.retry'))),
                ),
              ],
            ),
          ),
        ),
    ];
    final state = stateFor(context, c.phase, c.error,
        onRetry: c.load, hasData: c.items.isNotEmpty);
    Widget? body;
    if (state != null) {
      body = SizedBox(height: 320, child: state);
    } else if (c.items.isEmpty) {
      body = MailState(
        icon: Icons.inbox_outlined,
        title: c.filters.active ? l.t('mc.all.noMatch') : l.t('mc.all.empty'),
        text: c.filters.active
            ? l.t('mc.all.noMatchText')
            : l.t('mc.all.emptyText'),
        action: c.filters.active
            ? OutlinedButton(
                onPressed: () {
                  _search.clear();
                  c.apply(const MailAllInboxFilters());
                },
                child: Text(l.t('mc.all.clear')))
            : OutlinedButton.icon(
                onPressed: c.load,
                icon: const Icon(Icons.refresh),
                label: Text(l.t('mc.all.check'))),
      );
    }
    return RefreshIndicator(
      onRefresh: c.load,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          ...children,
          const SizedBox(height: AnumSpacing.xs),
          if (body != null)
            body
          else ...[
            Semantics(
              liveRegion: true,
              child: Text(
                l.f('mc.all.shown',
                    {'shown': c.items.length, 'total': c.total}),
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            for (final item in c.items)
              _AllInboxTile(
                item: item,
                canStar: (widget.mail.setup?.canAct ?? false) &&
                    (_account(item.accountId)?.canWrite ?? false),
                onOpen: () => _open(item),
                onStar: () async {
                  final failed = await c.toggleStar(item);
                  if (failed != null && context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                      content: Text(failed),
                      backgroundColor: Theme.of(context).colorScheme.error,
                    ));
                  }
                },
              ),
            if (c.moreError != null)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(l.f('mc.all.moreFailed', {'reason': c.moreError!}),
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
            if (c.nextCursor != null)
              Align(
                child: OutlinedButton(
                  onPressed: c.loadingMore ? null : c.loadMore,
                  child: Text(l.t('mc.all.more')),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

class _AllInboxTile extends StatelessWidget {
  const _AllInboxTile({
    required this.item,
    required this.canStar,
    required this.onOpen,
    required this.onStar,
  });
  final MailAllInboxMessage item;
  final bool canStar;
  final VoidCallback onOpen, onStar;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final m = item.message;
    final theme = Theme.of(context);
    final shared = item.access != 'owner';
    final subject = m.subject.isEmpty ? l.t('mc.noSubject') : m.subject;
    final sender = Text(
      m.sender?.label ?? '',
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: TextStyle(fontWeight: m.seen ? null : FontWeight.w700),
    );
    final date =
        Text(mailWhen(context, m.date), style: theme.textTheme.bodySmall);
    // With large text on a narrow phone the date would push the sender off the row: it moves under it.
    final compact = MediaQuery.sizeOf(context).width /
            MediaQuery.textScalerOf(context).scale(1) <
        360;
    return Card(
      margin: const EdgeInsets.only(top: AnumSpacing.xs),
      child: ListTile(
        onTap: onOpen,
        leading: canStar
            ? IconButton(
                tooltip: m.flagged ? l.t('mc.unstar') : l.t('mc.star'),
                onPressed: onStar,
                icon: Icon(m.flagged ? Icons.star : Icons.star_border),
              )
            : null,
        title: compact
            ? sender
            : Row(
                children: [
                  Expanded(child: sender),
                  const SizedBox(width: AnumSpacing.xs),
                  date,
                ],
              ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (compact) date,
            Text(subject, maxLines: 1, overflow: TextOverflow.ellipsis),
            const SizedBox(height: AnumSpacing.xxs),
            Semantics(
              label: shared
                  ? l.f('mc.all.badgeShared', {'mailbox': item.mailbox})
                  : l.f('mc.all.badge', {'mailbox': item.mailbox}),
              excludeSemantics: true,
              child: Container(
                padding: const EdgeInsets.symmetric(
                    horizontal: AnumSpacing.xs, vertical: 2),
                decoration: BoxDecoration(
                  border: Border.all(color: theme.dividerColor),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(shared ? Icons.group_outlined : Icons.inbox_outlined,
                        size: 14),
                    const SizedBox(width: AnumSpacing.xxs),
                    Flexible(
                      child: Text(item.mailbox,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.labelSmall),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
