// The phone's Notifications screen (docs/team-work.md#notifications-page),
// the same job as the web's Notifications page: filters by read state and by
// kind (with counts from GET /notifications/kinds), cursor paging, mark read
// or unread, delete with a confirmation, mark all read, and opening one goes
// where it points. Every row is the caller's own: the API answers 404 for
// anyone else's id.
import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';

import '../../data/api_client.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../admin/owned_controller.dart';
import 'notifications_strings.dart';
import 'team_controllers.dart';
import 'team_models.dart';
import 'team_repository.dart';
import 'team_screens.dart';

/// The read-state filter: every notification, only unread or only read.
enum NotificationState { all, unread, read }

/// The list, its filters and its paging. Actions update the loaded rows in
/// place (a row that no longer matches the read-state filter leaves the
/// list), so paging never jumps back to the first page.
class NotificationCenterController extends ChangeNotifier {
  NotificationCenterController(this.repository);
  final TeamRepository repository;

  TeamPhase phase = TeamPhase.loading;
  NotificationState state = NotificationState.all;
  String? kind;
  List<TeamNotification> items = [];
  List<NotificationKindCount> kinds = [];
  int unread = 0;
  String? nextCursor;
  String? error;
  String? kindsError;
  bool loadingMore = false;
  String? moreError;

  /// The id of the row an action is running on.
  String? busyId;
  bool _disposed = false;
  int _generation = 0;

  bool get filtered => state != NotificationState.all || kind != null;

  String? get _stateParam => switch (state) {
        NotificationState.all => null,
        NotificationState.unread => 'unread',
        NotificationState.read => 'read',
      };

  Future<void> load() async {
    final generation = ++_generation;
    phase = TeamPhase.loading;
    moreError = null;
    _notify();
    final kindsLoad = _loadKinds();
    try {
      final page =
          await repository.notifications(state: _stateParam, kind: kind);
      if (generation != _generation) return;
      items = page.items;
      unread = page.unread;
      nextCursor = page.nextCursor;
      phase = TeamPhase.ready;
      error = null;
    } catch (e) {
      if (generation != _generation) return;
      phase = e is ApiException && e.isPermissionDenied
          ? TeamPhase.denied
          : TeamPhase.error;
      error = describeTeamError(e);
    }
    await kindsLoad;
    _notify();
  }

  Future<void> _loadKinds() async {
    try {
      kinds = await repository.notificationKinds();
      kindsError = null;
    } catch (e) {
      kindsError = describeTeamError(e);
    }
  }

  Future<void> setReadState(NotificationState value) {
    if (value == state) return Future.value();
    state = value;
    return load();
  }

  Future<void> setKind(String? value) {
    if (value == kind) return Future.value();
    kind = value;
    return load();
  }

  Future<void> showEverything() {
    state = NotificationState.all;
    kind = null;
    return load();
  }

  Future<void> loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    final generation = _generation;
    loadingMore = true;
    moreError = null;
    _notify();
    try {
      final page = await repository.notifications(
          cursor: cursor, state: _stateParam, kind: kind);
      if (generation != _generation) return;
      items = [...items, ...page.items];
      unread = page.unread;
      nextCursor = page.nextCursor;
    } catch (e) {
      // A cursor the server no longer knows (its row was deleted) asks for
      // a reload; the message says so.
      moreError = describeTeamError(e);
    } finally {
      loadingMore = false;
      _notify();
    }
  }

  bool _matches(TeamNotification n) => switch (state) {
        NotificationState.all => true,
        NotificationState.unread => !n.read,
        NotificationState.read => n.read,
      };

  void _replace(TeamNotification updated) {
    final next = <TeamNotification>[];
    for (final n in items) {
      if (n.id != updated.id) {
        next.add(n);
      } else if (_matches(updated)) {
        next.add(updated);
      }
    }
    items = next;
  }

  /// Runs [action] for one row; null when it worked, else what failed.
  Future<String?> _act(TeamNotification n, Future<void> Function() action,
      void Function() apply) async {
    busyId = n.id;
    _notify();
    try {
      await action();
      apply();
      await _loadKinds();
      return null;
    } catch (e) {
      return describeTeamError(e);
    } finally {
      busyId = null;
      _notify();
    }
  }

  Future<String?> setRead(TeamNotification n, bool read) {
    if (n.read == read) return Future.value();
    return _act(
      n,
      () => read ? repository.markRead(n.id) : repository.markUnread(n.id),
      () {
        _replace(n.withRead(read));
        unread = (unread + (read ? -1 : 1)).clamp(0, 1 << 30);
      },
    );
  }

  Future<String?> delete(TeamNotification n) => _act(
        n,
        () => repository.deleteNotification(n.id),
        () {
          items = [
            for (final row in items)
              if (row.id != n.id) row
          ];
          if (!n.read) unread = (unread - 1).clamp(0, 1 << 30);
        },
      );

  Future<String?> markAllRead() async {
    try {
      await repository.markAllRead();
    } catch (e) {
      return describeTeamError(e);
    }
    await load();
    return null;
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

class NotificationsScreen extends StatelessWidget {
  const NotificationsScreen({required this.repository, super.key});
  final TeamRepository repository;

  /// Where a notice that is not about a work item opens, by its kind. The
  /// app registers these (for example the post-close trading report opens
  /// Finance, Trading intelligence; docs/finance-trading-intel.md#daily-report).
  static final Map<String, Future<void> Function(BuildContext context)>
      openers = {};

  /// The app's named route for a kind that has no opener (AnumRoutes and the
  /// tool and record routes in src/anum_app.dart), the phone's side of the
  /// web's `notificationHash`. Null: there is nowhere better than the row.
  static String? routeFor(String kind) => switch (kind) {
        'calendar_reminder' => '/calendar',
        'approval_reminder' => '/approvals',
        'system_health' || 'system_restart' => '/system-health',
        'document_expiry' => '/vault',
        'vehicle_reminder' => '/vehicles',
        'purchase_reminder' => '/purchases',
        'attendance_missed_check' => '/attendance',
        'missed_call' => '/calls',
        _ when kind.startsWith('finance_') => '/finance',
        _ => null,
      };

  @override
  Widget build(BuildContext context) =>
      OwnedController<NotificationCenterController>(
        create: () => NotificationCenterController(repository)..load(),
        builder: (context, c) =>
            _NotificationsView(controller: c, repository: repository),
      );
}

class _NotificationsView extends StatelessWidget {
  const _NotificationsView(
      {required this.controller, required this.repository});
  final NotificationCenterController controller;
  final TeamRepository repository;

  void _say(BuildContext context, String text) =>
      ScaffoldMessenger.maybeOf(context)
        ?..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(text)));

  Future<void> _open(BuildContext context, TeamNotification n) async {
    final s = NotificationText.of(context);
    if (!n.read) {
      final failed = await controller.setRead(n, true);
      if (failed != null && context.mounted) {
        _say(context, s.f('actionFailed', {'message': failed}));
      }
    }
    if (!context.mounted) return;
    final id = n.workItemId;
    final opener = NotificationsScreen.openers[n.kind];
    final route = NotificationsScreen.routeFor(n.kind);
    if (id != null) {
      await Navigator.push<void>(
        context,
        MaterialPageRoute(
          builder: (_) => WorkDetailScreen(repository: repository, itemId: id),
        ),
      );
    } else if (opener != null) {
      await opener(context);
    } else if (route != null) {
      await Navigator.of(context).pushNamed(route);
    }
  }

  Future<void> _delete(BuildContext context, TeamNotification n) async {
    final s = NotificationText.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(s.t('deleteTitle')),
        content: Text(s.f('deleteText', {'title': n.title})),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: Text(s.t('cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialog, true),
            child: Text(s.t('delete')),
          ),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    final failed = await controller.delete(n);
    if (!context.mounted) return;
    _say(
      context,
      failed == null
          ? s.t('deleted')
          : s.f('actionFailed', {'message': failed}),
    );
  }

  Future<void> _toggle(BuildContext context, TeamNotification n) async {
    final s = NotificationText.of(context);
    final failed = await controller.setRead(n, !n.read);
    if (failed != null && context.mounted) {
      _say(context, s.f('actionFailed', {'message': failed}));
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final s = NotificationText.of(context);
          final c = controller;
          return Scaffold(
            appBar: AppBar(
              title: Text(s.t('title')),
              actions: [
                if (c.phase == TeamPhase.ready && c.unread > 0)
                  IconButton(
                    tooltip: s.t('readAll'),
                    onPressed: () async {
                      final failed = await c.markAllRead();
                      if (failed != null && context.mounted) {
                        _say(context, s.f('actionFailed', {'message': failed}));
                      }
                    },
                    icon: const Icon(Icons.done_all),
                  ),
                IconButton(
                  tooltip: s.t('refresh'),
                  onPressed: c.phase == TeamPhase.loading ? null : c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            body: SafeArea(
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 860),
                  child: _body(context, s),
                ),
              ),
            ),
          );
        },
      );

  Widget _body(BuildContext context, NotificationText s) {
    final c = controller;
    if (c.phase == TeamPhase.denied) {
      return AnumFeedback(
        kind: AnumFeedbackKind.permission,
        message: s.f('denied', {'message': c.error ?? ''}),
      );
    }
    final list = switch (c.phase) {
      TeamPhase.loading when c.items.isEmpty => AnumFeedback(
          kind: AnumFeedbackKind.loading,
          message: s.t('loading'),
        ),
      TeamPhase.error => AnumFeedback(
          kind: AnumFeedbackKind.error,
          message: s.f('error', {'message': c.error ?? ''}),
          onRetry: c.load,
        ),
      _ when c.items.isEmpty => _Empty(controller: c),
      _ => RefreshIndicator(
          onRefresh: c.load,
          child: ListView.builder(
            padding: const EdgeInsets.only(bottom: AnumSpacing.lg),
            itemCount: c.items.length + 1,
            itemBuilder: (context, i) => i == c.items.length
                ? _Footer(controller: c)
                : _Row(
                    notification: c.items[i],
                    busy: c.busyId == c.items[i].id,
                    onOpen: () => _open(context, c.items[i]),
                    onToggle: () => _toggle(context, c.items[i]),
                    onDelete: () => _delete(context, c.items[i]),
                  ),
          ),
        ),
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _Filters(controller: c),
        if (c.phase == TeamPhase.loading && c.items.isNotEmpty)
          const LinearProgressIndicator(minHeight: 2),
        Expanded(child: list),
      ],
    );
  }
}

/// Read state (segmented) and kind (chips with counts).
class _Filters extends StatelessWidget {
  const _Filters({required this.controller});
  final NotificationCenterController controller;

  @override
  Widget build(BuildContext context) {
    final s = NotificationText.of(context);
    final c = controller;
    final p = context.palette;
    final total = c.kinds.fold<int>(0, (sum, k) => sum + k.total);
    final kindsUnread = c.kinds.fold<int>(0, (sum, k) => sum + k.unread);
    Widget chip(String? kind, String label, int count, int unread) {
      final selected = c.kind == kind;
      return Padding(
        padding: const EdgeInsetsDirectional.only(end: AnumSpacing.xs),
        child: Semantics(
          label: s.f('chipSemantics',
              {'label': label, 'count': count, 'unread': unread}),
          selected: selected,
          button: true,
          excludeSemantics: true,
          child: ChoiceChip(
            key: Key('ntf-kind-${kind ?? 'all'}'),
            label: Text(s.f('chip', {'label': label, 'count': count})),
            selected: selected,
            avatar: unread > 0 && !selected
                ? Icon(Icons.circle, size: 8, color: p.violet)
                : null,
            onSelected: (_) => c.setKind(kind),
          ),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(
          AnumSpacing.md, AnumSpacing.sm, AnumSpacing.md, AnumSpacing.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  c.unread > 0
                      ? s.f('unread', {'count': c.unread})
                      : s.t('allRead'),
                  style: TextStyle(color: p.muted, fontSize: AnumType.smPlus),
                ),
              ),
            ],
          ),
          const SizedBox(height: AnumSpacing.xs),
          Semantics(
            label: s.t('stateLabel'),
            container: true,
            child: SegmentedButton<NotificationState>(
              key: const Key('ntf-state'),
              showSelectedIcon: false,
              segments: [
                for (final v in NotificationState.values)
                  ButtonSegment(value: v, label: Text(s.t('state.${v.name}'))),
              ],
              selected: {c.state},
              onSelectionChanged: (v) => c.setReadState(v.first),
            ),
          ),
          if (c.kinds.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xs),
            Semantics(
              label: s.t('kindsLabel'),
              container: true,
              child: SizedBox(
                height: 48,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  children: [
                    chip(null, s.t('kind.all'), total, kindsUnread),
                    for (final k in c.kinds)
                      chip(k.kind, s.kind(k.kind), k.total, k.unread),
                  ],
                ),
              ),
            ),
          ],
          if (c.kindsError != null)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xxs),
              child: Text(
                s.f('kindsError', {'message': c.kindsError!}),
                style: TextStyle(color: p.warn, fontSize: AnumType.smPlus),
              ),
            ),
        ],
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.controller});
  final NotificationCenterController controller;

  @override
  Widget build(BuildContext context) {
    final s = NotificationText.of(context);
    final filtered = controller.filtered;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AnumSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.inbox_outlined, size: 32, color: context.palette.muted),
            const SizedBox(height: AnumSpacing.sm),
            Text(s.t(filtered ? 'emptyFilter' : 'empty'),
                textAlign: TextAlign.center),
            const SizedBox(height: AnumSpacing.md),
            OutlinedButton(
              onPressed: filtered ? controller.showEverything : controller.load,
              child: Text(s.t(filtered ? 'showEvery' : 'refresh')),
            ),
          ],
        ),
      ),
    );
  }
}

class _Footer extends StatelessWidget {
  const _Footer({required this.controller});
  final NotificationCenterController controller;

  @override
  Widget build(BuildContext context) {
    final s = NotificationText.of(context);
    final c = controller;
    return Padding(
      padding: const EdgeInsets.all(AnumSpacing.md),
      child: Column(
        children: [
          if (c.moreError != null)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: Text(
                s.f('error', {'message': c.moreError!}),
                textAlign: TextAlign.center,
                style: TextStyle(color: context.palette.stop),
              ),
            ),
          if (c.loadingMore)
            Semantics(
              liveRegion: true,
              child: Text(s.t('loadingMore')),
            )
          else if (c.nextCursor != null)
            OutlinedButton(
              onPressed: c.loadMore,
              child: Text(s.t('loadMore')),
            ),
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({
    required this.notification,
    required this.busy,
    required this.onOpen,
    required this.onToggle,
    required this.onDelete,
  });
  final TeamNotification notification;
  final bool busy;
  final VoidCallback onOpen, onToggle, onDelete;

  @override
  Widget build(BuildContext context) {
    final s = NotificationText.of(context);
    final p = context.palette;
    final n = notification;
    final m = MaterialLocalizations.of(context);
    final local = n.createdAt.toLocal();
    final when =
        '${m.formatMediumDate(local)} ${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
    final kind = s.kind(n.kind);
    final toggleLabel = n.read ? s.t('markUnread') : s.t('markRead');
    return Semantics(
      // Read and unread are said with the row; the custom actions reach the
      // menu's items without opening it (TalkBack and VoiceOver actions).
      customSemanticsActions: busy
          ? null
          : {
              CustomSemanticsAction(label: toggleLabel): onToggle,
              CustomSemanticsAction(label: s.t('delete')): onDelete,
            },
      child: ListTile(
        key: Key('ntf-${n.id}'),
        onTap: busy ? null : onOpen,
        leading: Icon(
          n.read ? Icons.notifications_none : Icons.notifications_active,
          color: n.read ? p.muted : p.violet,
          semanticLabel: n.read ? s.t('stateRead') : s.t('stateUnread'),
        ),
        title: Text(
          n.title,
          style: TextStyle(
            fontWeight: n.read ? FontWeight.normal : FontWeight.w700,
          ),
        ),
        subtitle: Text('${n.body}\n$kind · $when'),
        isThreeLine: true,
        trailing: busy
            ? const SizedBox.square(
                dimension: 24,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : PopupMenuButton<String>(
                tooltip: s.f('moreFor', {'title': n.title}),
                onSelected: (v) => v == 'delete' ? onDelete() : onToggle(),
                itemBuilder: (_) => [
                  PopupMenuItem(value: 'toggle', child: Text(toggleLabel)),
                  PopupMenuItem(value: 'delete', child: Text(s.t('delete'))),
                ],
              ),
      ),
    );
  }
}
