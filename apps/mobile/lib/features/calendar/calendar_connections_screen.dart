import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/readable_error.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'calendar_connections.dart';
import 'calendar_models.dart';

/// Calendar connections on the phone (docs/calendar.md#connecting-on-the-phone):
/// the providers the server can connect, each connected account with its
/// health, Sync now, Choose calendars and Disconnect. Owners act; everyone
/// else sees the status and who can change it.
class CalendarConnectionsScreen extends StatefulWidget {
  const CalendarConnectionsScreen({required this.controller, super.key});
  final CalendarConnectionsController controller;

  @override
  State<CalendarConnectionsScreen> createState() =>
      _CalendarConnectionsScreenState();
}

class _CalendarConnectionsScreenState extends State<CalendarConnectionsScreen>
    with WidgetsBindingObserver {
  CalendarConnectionsController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    c.addListener(_announce);
    Future.microtask(c.load);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    c.removeListener(_announce);
    super.dispose();
  }

  /// The provider's sign-in returns to the app (its own scheme brings it to
  /// the front): finish the request this phone started.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && c.step == ConnectStep.waiting) {
      c.finish(auto: true);
    }
  }

  void _announce() {
    final notice = c.notice;
    if (notice == null || !mounted) return;
    c.clearNotice();
    final l = AnumLocalizations.maybeOf(context);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(l.f(notice.key, notice.args)),
        backgroundColor: c.noticeIsError ? context.palette.stop : null));
  }

  Future<void> _disconnect(SyncConnection connection) async {
    final l = AnumLocalizations.maybeOf(context);
    final sure = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(l.t('calconn.disconnectTitle')),
        content: Text(l.f('calconn.disconnectText', {
          'email': connection.email,
          'provider': connection.providerName,
        })),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(l.t('calconn.cancel'))),
          FilledButton(
              key: const Key('calconn-disconnect-confirm'),
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(l.t('calconn.disconnect'))),
        ],
      ),
    );
    if (sure == true) await c.disconnect(connection);
  }

  Future<void> _choose(SyncConnection connection) async {
    final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(
        builder: (_) => CalendarChoicesScreen(
            repository: c.repository, connection: connection)));
    if (saved == true) await c.load();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.t('calconn.title'))),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) => AnumPage(onRefresh: c.load, children: [
          AnumHeader(
              title: l.t('calconn.title'), subtitle: l.t('calconn.subtitle')),
          ..._body(context, l),
        ]),
      ),
    );
  }

  List<Widget> _body(BuildContext context, AnumLocalizations l) {
    final p = context.palette;
    switch (c.phase) {
      case ConnectionsPhase.loading:
        return [
          Semantics(
            liveRegion: true,
            label: l.t('calconn.loading'),
            child: const Center(
                child: Padding(
                    padding: EdgeInsets.all(AnumSpacing.lg),
                    child: CircularProgressIndicator())),
          ),
        ];
      case ConnectionsPhase.denied:
        return [
          AnumEmpty(
              key: const Key('calconn-denied'),
              title: l.t('calconn.deniedTitle'),
              message: c.error ?? l.t('calconn.denied'),
              icon: Icons.lock_outline),
        ];
      case ConnectionsPhase.error:
        return [
          AnumEmpty(
              key: const Key('calconn-error'),
              title: l.t('calconn.loadFailed'),
              message: c.error ?? '',
              icon: Icons.cloud_off_outlined,
              action: l.t('calconn.retry'),
              onAction: c.load),
        ];
      case ConnectionsPhase.ready:
        break;
    }
    final status = c.status!;
    return [
      if (!status.canManage)
        Text(l.t('calconn.ownersOnly'),
            key: const Key('calconn-owners-only'),
            style: TextStyle(color: p.muted)),
      if (c.step != ConnectStep.idle) _ConnectProgress(controller: c),
      if (c.step == ConnectStep.idle && c.connectError != null)
        Semantics(
          liveRegion: true,
          child: AnumSurface(
            accent: p.stop,
            child: Text(
                c.connectError!.startsWith('calconn.')
                    ? l.t(c.connectError!)
                    : l.f('calconn.connectFailed', {'reason': c.connectError!}),
                key: const Key('calconn-connect-error')),
          ),
        ),
      AnumHeader(title: l.t('calconn.connections')),
      if (status.connections.isEmpty)
        AnumEmpty(
            key: const Key('calconn-empty'),
            title: l.t('calconn.none'),
            message: status.canManage
                ? l.t('calconn.noneOwner')
                : l.t('calconn.noneOther'),
            icon: Icons.event_repeat_outlined)
      else
        for (final connection in status.connections)
          _ConnectionCard(
            connection: connection,
            canManage: status.canManage,
            busy: c.busy,
            onSync: () => c.syncNow(connection),
            onChoose: () => _choose(connection),
            onDisconnect: () => _disconnect(connection),
            onReconnect: () {
              final provider = status.providers
                  .where(
                      (p) => p.provider == connection.provider && p.configured)
                  .firstOrNull;
              if (provider != null) c.connect(provider);
            },
          ),
      AnumHeader(title: l.t('calconn.providers')),
      for (final provider in status.providers)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(provider.name,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            if (!provider.configured)
              Text(
                  l.f('calconn.notSetUp',
                      {'settings': provider.missing.join(', ')}),
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm))
            else if (status.canManage) ...[
              const SizedBox(height: AnumSpacing.xs),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  key: Key('calconn-connect-${provider.provider}'),
                  onPressed: c.step == ConnectStep.idle
                      ? () => c.connect(provider)
                      : null,
                  icon: const Icon(Icons.link, size: 18),
                  label: Text(status.connections
                          .any((x) => x.provider == provider.provider)
                      ? l.f(
                          'calconn.connectAnother', {'provider': provider.name})
                      : l.f('calconn.connect', {'provider': provider.name})),
                ),
              ),
            ],
          ]),
        ),
      Text(l.t('calconn.rule'),
          style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
    ];
  }
}

/// The sign-in is open in the browser: what to do now, finish by hand and
/// cancel. Finishing also happens on its own when the app comes back.
class _ConnectProgress extends StatelessWidget {
  const _ConnectProgress({required this.controller});
  final CalendarConnectionsController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final working =
        c.step == ConnectStep.opening || c.step == ConnectStep.finishing;
    return Semantics(
      liveRegion: true,
      child: AnumSurface(
        key: const Key('calconn-progress'),
        accent: p.sky,
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(
              switch (c.step) {
                ConnectStep.opening => l.f('calconn.opening',
                    {'provider': c.connectingProvider ?? ''}),
                ConnectStep.finishing => l.t('calconn.finishing'),
                _ => l.f('calconn.waiting',
                    {'provider': c.connectingProvider ?? ''}),
              },
              style: TextStyle(color: p.text)),
          if (c.connectError != null) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(c.connectError!,
                key: const Key('calconn-waiting-error'),
                style: TextStyle(color: p.stop, fontSize: AnumType.sm)),
          ],
          const SizedBox(height: AnumSpacing.xs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton(
              key: const Key('calconn-finish'),
              onPressed: working ? null : () => c.finish(),
              child: Text(l.t('calconn.finish')),
            ),
            TextButton(
              key: const Key('calconn-cancel-connect'),
              onPressed: working ? null : c.cancelConnect,
              child: Text(l.t('calconn.cancel')),
            ),
          ]),
        ]),
      ),
    );
  }
}

class _ConnectionCard extends StatelessWidget {
  const _ConnectionCard({
    required this.connection,
    required this.canManage,
    required this.busy,
    required this.onSync,
    required this.onChoose,
    required this.onDisconnect,
    required this.onReconnect,
  });

  final SyncConnection connection;
  final bool canManage;
  final String? busy;
  final VoidCallback onSync, onChoose, onDisconnect, onReconnect;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final extra = connection.extraCalendars;
    final syncing = busy == 'sync:${connection.id}';
    return AnumSurface(
      key: Key('calconn-${connection.id}'),
      accent: connection.healthy ? null : p.warn,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Icon(connection.healthy ? Icons.event_available : Icons.sync_problem,
              size: 20, color: connection.healthy ? p.ok : p.warn),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
            child: Text(connection.email,
                textDirection: TextDirection.ltr,
                style: const TextStyle(fontWeight: FontWeight.w600)),
          ),
        ]),
        Text(
            '${connection.providerName} · ${l.t('calconn.status.${connection.status}')} · ${l.f('calconn.events', {
                  'count': connection.eventsSynced
                })}',
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        if (extra.isNotEmpty)
          Text(
              l.f('calconn.also',
                  {'names': extra.map((c) => c.name).join(', ')}),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        if (connection.calendarErrors.isNotEmpty)
          Text(
              l.f('calconn.readFailed',
                  {'reasons': connection.calendarErrors.join('; ')}),
              style: TextStyle(color: p.warn, fontSize: AnumType.sm)),
        if (!connection.healthy) ...[
          if (connection.lastError != null)
            Text(l.f('calsync.error', {'reason': connection.lastError!}),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          Text(
              l.f('calconn.fix.${connection.status}',
                  {'provider': connection.providerName}),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        ],
        if (canManage) ...[
          const SizedBox(height: AnumSpacing.xs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            if (connection.needsReconnect)
              FilledButton.icon(
                key: Key('calconn-reconnect-${connection.id}'),
                onPressed: busy == null ? onReconnect : null,
                icon: const Icon(Icons.link, size: 18),
                label: Text(l.f('calconn.reconnect',
                    {'provider': connection.providerName})),
              )
            else ...[
              OutlinedButton.icon(
                key: Key('calconn-sync-${connection.id}'),
                onPressed: busy == null ? onSync : null,
                icon: const Icon(Icons.sync, size: 18),
                label:
                    Text(syncing ? l.t('calsync.syncing') : l.t('calsync.now')),
              ),
              OutlinedButton.icon(
                key: Key('calconn-choose-${connection.id}'),
                onPressed: busy == null ? onChoose : null,
                icon: const Icon(Icons.checklist, size: 18),
                label: Text(l.t('calconn.choose')),
              ),
            ],
            TextButton.icon(
              key: Key('calconn-disconnect-${connection.id}'),
              onPressed: busy == null ? onDisconnect : null,
              icon: const Icon(Icons.link_off, size: 18),
              label: Text(l.t('calconn.disconnect')),
            ),
          ]),
        ],
      ]),
    );
  }
}

/// Which calendars of one account sync. The list comes fresh from the
/// provider; the primary calendar always syncs both ways, the others ticked
/// are read into ANUM only. Pops `true` once saved.
class CalendarChoicesScreen extends StatefulWidget {
  const CalendarChoicesScreen(
      {required this.repository, required this.connection, super.key});
  final CalendarConnectionsRepository repository;
  final SyncConnection connection;

  @override
  State<CalendarChoicesScreen> createState() => _CalendarChoicesScreenState();
}

class _CalendarChoicesScreenState extends State<CalendarChoicesScreen> {
  CalendarChoices? choices;
  String? loadError, saveError;
  bool loading = true, saving = false;
  final picked = <String>{};

  @override
  void initState() {
    super.initState();
    _load();
  }

  String _describe(Object e) =>
      e is ApiException ? e.readable : readableError(e);

  Future<void> _load() async {
    setState(() {
      loading = true;
      loadError = null;
    });
    try {
      final found = await widget.repository.calendars(widget.connection.id);
      if (!mounted) return;
      setState(() {
        choices = found;
        picked
          ..clear()
          ..addAll([
            for (final c in found.calendars)
              if (c.selected) c.id
          ]);
        loading = false;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        loadError = _describe(e);
        loading = false;
      });
    }
  }

  Future<void> _save() async {
    final l = AnumLocalizations.maybeOf(context);
    final found = choices!;
    final ids = [
      for (final c in found.calendars)
        if (!c.primary && picked.contains(c.id)) c.id
    ];
    if (ids.length > found.maxExtra) {
      setState(
          () => saveError = l.f('calconn.tooMany', {'max': found.maxExtra}));
      return;
    }
    setState(() {
      saving = true;
      saveError = null;
    });
    try {
      await widget.repository.choose(widget.connection.id, ids);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(l.f('calconn.saved',
              {'count': ids.length, 'email': widget.connection.email}))));
      Navigator.of(context).pop(true);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        saveError = _describe(e);
        saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final found = choices;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('calconn.choose'))),
      body: AnumPage(onRefresh: _load, children: [
        AnumHeader(
            title:
                l.f('calconn.chooseTitle', {'email': widget.connection.email}),
            subtitle: l.t('calconn.chooseLead')),
        if (loading)
          Semantics(
            liveRegion: true,
            label: l.f('calconn.listing',
                {'provider': widget.connection.providerName}),
            child: const Center(
                child: Padding(
                    padding: EdgeInsets.all(AnumSpacing.lg),
                    child: CircularProgressIndicator())),
          )
        else if (loadError != null)
          AnumEmpty(
              key: const Key('calchoice-error'),
              title: l.t('calconn.listFailed'),
              message: loadError!,
              icon: Icons.cloud_off_outlined,
              action: l.t('calconn.retry'),
              onAction: _load)
        else if (found == null || found.calendars.isEmpty)
          AnumEmpty(
              key: const Key('calchoice-empty'),
              title: l.t('calconn.listEmpty'),
              message: l.f('calconn.listEmptyText',
                  {'provider': widget.connection.providerName}),
              icon: Icons.event_busy_outlined,
              action: l.t('calconn.retry'),
              onAction: _load)
        else ...[
          AnumSurface(
            padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final calendar in found.calendars)
                CheckboxListTile(
                  key: Key('calchoice-${calendar.id}'),
                  value: calendar.primary || picked.contains(calendar.id),
                  onChanged: calendar.primary || saving
                      ? null
                      : (on) => setState(() => on == true
                          ? picked.add(calendar.id)
                          : picked.remove(calendar.id)),
                  title: Text(calendar.name),
                  subtitle: Text(calendar.primary
                      ? l.t('calconn.primary')
                      : l.t('calconn.readOnly')),
                  secondary: Icon(Icons.circle,
                      size: 14, color: _color(calendar.color) ?? p.muted),
                ),
            ]),
          ),
          if (saveError != null)
            Semantics(
              liveRegion: true,
              child: Text(saveError!,
                  key: const Key('calchoice-save-error'),
                  style: TextStyle(color: p.stop)),
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton(
              key: const Key('calchoice-save'),
              onPressed: saving ? null : _save,
              child: Text(saving ? l.t('calconn.saving') : l.t('calconn.save')),
            ),
          ),
        ],
      ]),
    );
  }

  static Color? _color(String? hex) {
    if (hex == null || !RegExp(r'^#[0-9A-Fa-f]{6}$').hasMatch(hex)) return null;
    return Color(int.parse('FF${hex.substring(1)}', radix: 16));
  }
}
