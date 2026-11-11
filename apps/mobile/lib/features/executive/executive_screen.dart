import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../owner_desk/desk_widgets.dart';
import 'executive_data.dart';

/// The executive desk on a phone (docs/ANUM_MINE_EXECUTIVE_PA.md#mobile):
/// workspace cards in one column (two from 760 px), the grants of the
/// current workspace, and today's briefing. The web's offices tab and the
/// headquarters assistant stay on the web (they work inside the 3D office).
class ExecutiveScreen extends StatefulWidget {
  const ExecutiveScreen(
      {required this.controller,
      this.onOpenTasks,
      this.onOpenSettings,
      super.key});
  final ExecutiveController controller;
  final VoidCallback? onOpenTasks, onOpenSettings;

  @override
  State<ExecutiveScreen> createState() => _ExecutiveScreenState();
}

class _ExecutiveScreenState extends State<ExecutiveScreen> {
  ExecutiveController get c => widget.controller;
  final _grantee = TextEditingController();
  bool _creating = false;
  final _scopes = <String>{'counts'};
  DateTime? _expiry;
  String? _narrowing;
  final _narrowScopes = <String>{};
  bool _started = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_started) {
      _started = true;
      c.language = AnumLocalizations.of(context).isArabic ? 'ar' : 'en';
      c.load();
    }
  }

  @override
  void dispose() {
    _grantee.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final l = AnumLocalizations.of(context);
          return Scaffold(
            appBar: AppBar(title: Text(l.t('nav.executive')), actions: [
              IconButton(
                  key: const Key('ex-refresh'),
                  tooltip: l.t('executive.refresh'),
                  onPressed: c.phase == ExecutivePhase.loading ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                  eyebrow: l.t('od.eyebrow'),
                  title: l.t('nav.executive'),
                  subtitle: l.t('executive.subtitle'),
                  large: true),
              if (c.note case final note?)
                DeskMessage(
                    key: const Key('ex-note'),
                    text: deskText(l, note.key, note.values),
                    failed: note.failed),
              ..._desk(context),
              _briefingCard(context),
              _grantsCard(context),
              AnumSurface(
                key: const Key('ex-web-only'),
                child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.desktop_windows_outlined,
                          size: 18, color: context.palette.muted),
                      const SizedBox(width: AnumSpacing.sm),
                      Expanded(
                          child: Text(l.t('od.ex.officesWeb'),
                              style: TextStyle(
                                  color: context.palette.muted,
                                  fontSize: AnumType.sm))),
                    ]),
              ),
            ]),
          );
        },
      );

  List<Widget> _desk(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    switch (c.phase) {
      case ExecutivePhase.loading:
        return [DeskLoading(label: l.t('executive.loading'))];
      case ExecutivePhase.error:
        return [
          AnumSurface(
              accent: p.stop,
              child: AnumEmpty(
                  key: const Key('ex-error'),
                  icon: Icons.error_outline,
                  title: l.t('od.loadFailedTitle'),
                  message:
                      l.f('executive.loadFailed', {'message': c.error ?? '-'}),
                  action: l.t('od.retry'),
                  onAction: c.load))
        ];
      case ExecutivePhase.denied:
        return [
          AnumSurface(
              child: AnumEmpty(
                  key: const Key('ex-denied'),
                  icon: Icons.lock_outline,
                  title: l.t('executive.deniedTitle'),
                  message: l.t('executive.deniedText')))
        ];
      case ExecutivePhase.notConnected:
        return [
          AnumSurface(
              child: AnumEmpty(
                  key: const Key('ex-not-connected'),
                  icon: Icons.link_off,
                  title: l.t('executive.notConnectedTitle'),
                  message: l.t('executive.notConnectedText'))),
          _ownCard(context),
        ];
      case ExecutivePhase.ready:
        break;
    }
    final overview = c.overview!;
    if (overview.workspaces.isEmpty) {
      return [
        AnumSurface(
            child: AnumEmpty(
                key: const Key('ex-empty'),
                icon: Icons.apartment_outlined,
                title: l.t('executive.emptyTitle'),
                message: l.t('executive.emptyText'),
                action: widget.onOpenSettings == null
                    ? null
                    : l.t('executive.emptyAction'),
                onAction: widget.onOpenSettings))
      ];
    }
    Widget group(String id, String title, List<Widget> cards) => Column(
            key: Key('ex-group-$id'),
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              DeskSectionTitle(
                  title: title,
                  count: cards.length,
                  icon: Icons.verified_user_outlined),
              const SizedBox(height: AnumSpacing.sm),
              DeskColumns(children: cards),
            ]);
    final granted = overview.of(ExecutiveAccess.granted);
    final notGranted = overview.of(ExecutiveAccess.notGranted);
    final unavailable = overview.of(ExecutiveAccess.unavailable);
    return [
      if (overview.truncated)
        DeskMessage(text: l.t('od.ex.truncated'), failed: true),
      if (granted.isNotEmpty)
        group('granted', l.t('executive.group.granted'),
            [for (final w in granted) _card(context, w)]),
      if (notGranted.isNotEmpty)
        group('notGranted', l.t('executive.group.notGranted'),
            [for (final w in notGranted) _card(context, w)]),
      if (unavailable.isNotEmpty)
        group('unavailable', l.t('executive.group.unavailable'),
            [for (final w in unavailable) _card(context, w)]),
    ];
  }

  Widget _figures(BuildContext context, ExecutiveFigures f) {
    final l = AnumLocalizations.of(context);
    String value(int? n) => n == null ? l.t('executive.fig.notShared') : '$n';
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      DeskFact(label: l.t('executive.fig.tasks'), value: value(f.tasks)),
      DeskFact(label: l.t('executive.fig.blocked'), value: value(f.blocked)),
      DeskFact(
          label: l.t('executive.fig.approvals'), value: value(f.approvals)),
      DeskFact(
          label: l.t('executive.fig.costs'),
          value: f.costUsd == null
              ? l.t('executive.fig.notShared')
              : l.f('executive.cost', {'amount': f.costUsd!})),
      DeskFact(label: l.t('executive.fig.meetings'), value: value(f.meetings)),
      if (f.agents != null) ...[
        DeskFact(label: l.t('executive.fig.agents'), value: value(f.agents)),
        DeskFact(label: l.t('executive.fig.rooms'), value: value(f.rooms)),
      ],
    ]);
  }

  Widget _ownCard(BuildContext context) {
    final l = AnumLocalizations.of(context);
    return AnumSurface(
      key: const Key('ex-own'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
            title: l.t('executive.state.current'),
            trailing: AnumPill(
                label: l.t('executive.state.current'), tone: AnumTone.info)),
        const SizedBox(height: AnumSpacing.sm),
        if (c.own != null)
          _figures(context, c.own!)
        else
          Text(l.t('executive.fig.notShared'),
              style: TextStyle(color: context.palette.muted)),
        if (widget.onOpenTasks != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                  onPressed: widget.onOpenTasks,
                  child: Text(l.t('executive.openIn')))),
        ],
      ]),
    );
  }

  Widget _card(BuildContext context, ExecutiveWorkspace w) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final current = w.workspaceId == c.currentWorkspaceId;
    final (badge, tone) = switch (w.access) {
      ExecutiveAccess.granted => (
          current
              ? l.t('executive.state.current')
              : l.t('executive.state.granted'),
          current ? AnumTone.info : AnumTone.ok
        ),
      ExecutiveAccess.notGranted => (
          current
              ? l.t('executive.state.current')
              : w.reason == 'grant_ended'
                  ? l.t('executive.state.grantEnded')
                  : l.t('executive.state.notGranted'),
          current ? AnumTone.info : AnumTone.neutral
        ),
      ExecutiveAccess.unavailable => (
          l.t('executive.state.unavailable'),
          AnumTone.warn
        ),
    };
    final figures = w.access == ExecutiveAccess.granted
        ? w.figures
        : (current ? c.own : null);
    return AnumSurface(
      key: Key('ex-ws-${w.workspaceId}'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                  w.access == ExecutiveAccess.unavailable
                      ? w.workspaceId
                      : w.label,
                  style: TextStyle(
                      color: p.text,
                      fontSize: AnumType.base,
                      fontWeight: FontWeight.w800)),
              AnumPill(label: badge, tone: tone),
            ]),
        const SizedBox(height: AnumSpacing.sm),
        if (figures != null)
          _figures(context, figures)
        else
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(
                w.access == ExecutiveAccess.unavailable
                    ? Icons.link_off
                    : Icons.lock_outline,
                size: 16,
                color: p.muted),
            const SizedBox(width: AnumSpacing.xs),
            Expanded(
                child: Text(
                    l.t(w.access == ExecutiveAccess.unavailable
                        ? 'executive.unavailableText'
                        : 'executive.notGrantedText'),
                    style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
          ]),
        if (w.access == ExecutiveAccess.granted) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(
              [
                l.f('executive.asOf', {'time': deskWhen(context, w.asOf)}),
                if (w.expiresAt != null)
                  l.f('executive.expires',
                      {'time': deskWhen(context, w.expiresAt)}),
              ].join(' | '),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ],
        if (current && widget.onOpenTasks != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                  onPressed: widget.onOpenTasks,
                  child: Text(l.t('executive.openIn')))),
        ] else if (!current && w.canSwitch && c.switchWorkspace != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton(
                key: Key('ex-switch-${w.workspaceId}'),
                onPressed:
                    c.switching != null ? null : () => c.openWorkspace(w),
                child: Text(l.f('executive.switch', {'name': w.label}))),
          ),
          const SizedBox(height: AnumSpacing.xxs),
          Text(l.t('executive.switchNote'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ],
      ]),
    );
  }

  Widget _briefingCard(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final b = c.briefing;
    return AnumSurface(
      key: const Key('ex-briefing'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
            title: l.t('od.ex.briefing'),
            subtitle: b?.title.isNotEmpty == true ? b!.title : null,
            trailing: IconButton(
                tooltip: l.t('od.ex.briefingRefresh'),
                onPressed: c.briefingLoading ? null : c.loadBriefing,
                icon: const Icon(Icons.refresh))),
        const SizedBox(height: AnumSpacing.sm),
        if (c.briefingLoading && b == null)
          DeskLoading(label: l.t('od.ex.briefingLoading'))
        else if (c.briefingError != null)
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('od.loadFailedTitle'),
              message:
                  l.f('od.ex.briefingFailed', {'message': c.briefingError!}),
              action: l.t('od.retry'),
              onAction: c.loadBriefing)
        else if (b == null || b.sections.isEmpty)
          AnumEmpty(
              icon: Icons.wb_sunny_outlined,
              title: l.t('od.ex.briefingEmpty'),
              message: l.t('od.ex.briefingEmptyHint'))
        else
          for (final s in b.sections)
            Padding(
              key: Key('ex-brief-${s.key}'),
              padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Wrap(
                        spacing: AnumSpacing.xs,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          Text(s.title,
                              style: TextStyle(
                                  color: p.text, fontWeight: FontWeight.w800)),
                          if (s.state != 'ok')
                            AnumPill(
                                label: l.t('od.ex.section.${s.state}'),
                                tone: s.state == 'empty'
                                    ? AnumTone.neutral
                                    : AnumTone.warn),
                        ]),
                    if (s.headline.isNotEmpty)
                      Text(s.headline,
                          style:
                              TextStyle(color: p.muted, fontSize: AnumType.sm)),
                    for (final item in s.items.take(5))
                      Padding(
                        padding: const EdgeInsetsDirectional.only(
                            start: AnumSpacing.sm, top: AnumSpacing.xxs),
                        child: Text(
                            item.detail == null || item.detail!.isEmpty
                                ? item.label
                                : '${item.label}: ${item.detail}',
                            style: TextStyle(
                                color: p.text, fontSize: AnumType.sm)),
                      ),
                    if (s.truncated || s.items.length > 5)
                      Text(l.t('od.ex.more'),
                          style:
                              TextStyle(color: p.faint, fontSize: AnumType.xs)),
                    for (final n in s.notes)
                      Text(n,
                          style:
                              TextStyle(color: p.faint, fontSize: AnumType.xs)),
                  ]),
            ),
      ]),
    );
  }

  String _scopesText(AnumLocalizations l, List<String> scopes) =>
      scopes.map((s) => l.t('executive.scope.$s')).join(', ');

  Future<void> _pickExpiry() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
        context: context,
        firstDate: now,
        lastDate: now.add(const Duration(days: 364)),
        initialDate: _expiry ?? now.add(const Duration(days: 30)));
    if (picked != null) {
      setState(() => _expiry =
          DateTime(picked.year, picked.month, picked.day, 23, 59, 59));
    }
  }

  Widget _scopeBoxes(Set<String> chosen, List<String> allowed,
          AnumLocalizations l, String keyPrefix) =>
      Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        for (final scope in executiveScopes)
          if (allowed.contains(scope))
            CheckboxListTile(
              key: Key('$keyPrefix-$scope'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: chosen.contains(scope),
              title: Text(l.t('executive.scope.$scope')),
              onChanged: (on) => setState(
                  () => on == true ? chosen.add(scope) : chosen.remove(scope)),
            ),
        if (chosen.isEmpty)
          Text(l.t('executive.pickOne'),
              style: TextStyle(color: context.palette.stop)),
      ]);

  Widget _grantsCard(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final header = AnumHeader(
        title: l.t('executive.grants.title'),
        subtitle: c.grantsPhase == GrantsPhase.denied
            ? null
            : l.t('executive.grants.subtitle'));
    if (c.grantsPhase == GrantsPhase.denied) {
      return AnumSurface(
        key: const Key('ex-grants'),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          header,
          const SizedBox(height: AnumSpacing.sm),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.lock_outline, size: 16, color: p.muted),
            const SizedBox(width: AnumSpacing.xs),
            Expanded(
                child: Text(l.t('executive.grants.ownerOnly'),
                    key: const Key('ex-grants-owner-only'),
                    style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
          ]),
        ]),
      );
    }
    return AnumSurface(
      key: const Key('ex-grants'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        header,
        const SizedBox(height: AnumSpacing.sm),
        if (c.grantNote case final note?) ...[
          DeskMessage(
              key: const Key('ex-grant-note'),
              text: deskText(l, note.key, note.values),
              failed: note.failed),
          const SizedBox(height: AnumSpacing.sm),
        ],
        if (c.grantsPhase == GrantsPhase.loading && c.grantList.isEmpty)
          DeskLoading(label: l.t('od.loading'))
        else if (c.grantsPhase == GrantsPhase.error)
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('od.loadFailedTitle'),
              message: l.f('executive.grants.loadFailed',
                  {'message': c.grantsError ?? '-'}),
              action: l.t('od.retry'),
              onAction: c.loadGrants),
        if (!_creating)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
                key: const Key('ex-grant-new'),
                onPressed: () => setState(() => _creating = true),
                icon: const Icon(Icons.key_outlined),
                label: Text(l.t('executive.grants.new'))),
          )
        else
          _createForm(context),
        if (c.grantsPhase == GrantsPhase.ready && c.grantList.isEmpty) ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('executive.grants.empty'),
              key: const Key('ex-grants-empty'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        ],
        for (final g in c.grantList) ...[
          Divider(height: 28, color: p.line),
          _grantRow(context, g),
        ],
      ]),
    );
  }

  Widget _createForm(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return Semantics(
      container: true,
      label: l.t('executive.grants.new'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        TextField(
            key: const Key('ex-grantee'),
            controller: _grantee,
            maxLength: 160,
            autocorrect: false,
            onChanged: (_) => setState(() {}),
            decoration:
                InputDecoration(labelText: l.t('executive.grants.grantee'))),
        Text(l.t('executive.grants.scopes'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        _scopeBoxes(_scopes, executiveScopes, l, 'ex-scope'),
        const SizedBox(height: AnumSpacing.xs),
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              OutlinedButton.icon(
                  key: const Key('ex-expiry'),
                  onPressed: _pickExpiry,
                  icon: const Icon(Icons.event_outlined),
                  label: Text(_expiry == null
                      ? l.t('executive.grants.expiry')
                      : MaterialLocalizations.of(context)
                          .formatMediumDate(_expiry!))),
              if (_expiry != null)
                TextButton(
                    onPressed: () => setState(() => _expiry = null),
                    child: Text(l.t('od.ex.clearDate'))),
            ]),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton(
              key: const Key('ex-grant-submit'),
              onPressed:
                  c.grantBusy || _scopes.isEmpty || _grantee.text.trim().isEmpty
                      ? null
                      : () async {
                          final ok = await c.request(
                              _grantee.text, _ordered(_scopes), _expiry);
                          if (ok && mounted) {
                            setState(() {
                              _creating = false;
                              _grantee.clear();
                              _expiry = null;
                              _scopes
                                ..clear()
                                ..add('counts');
                            });
                          }
                        },
              child: Text(l.t('executive.grants.submit'))),
          TextButton(
              onPressed: () => setState(() => _creating = false),
              child: Text(l.t('executive.grants.cancel'))),
        ]),
      ]),
    );
  }

  List<String> _ordered(Set<String> scopes) => [
        for (final s in executiveScopes)
          if (scopes.contains(s)) s
      ];

  Widget _grantRow(BuildContext context, ExecutiveGrant g) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return Column(
      key: Key('ex-grant-${g.id}'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                  l.f('executive.grants.row', {
                    'grantee': g.granteeUserId,
                    'scopes': _scopesText(l, g.scopes)
                  }),
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
              AnumPill(
                  label: l.t('executive.status.${g.status}'),
                  tone: g.status == 'active'
                      ? AnumTone.ok
                      : g.status == 'pending'
                          ? AnumTone.warn
                          : AnumTone.neutral),
            ]),
        Text(
            [
              l.f('executive.grants.by', {'user': g.requestedBy}),
              deskWhen(context, g.requestedAt),
              if (g.expiresAt != null)
                l.f('executive.expires',
                    {'time': deskWhen(context, g.expiresAt)}),
            ].join(' | '),
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        if (g.status == 'pending') ...[
          const SizedBox(height: AnumSpacing.xs),
          DeskHash(label: l.t('executive.grants.hash'), hash: g.payloadHash),
          Text(l.t('executive.grants.hashNote'),
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton(
                key: Key('ex-grant-approve-${g.id}'),
                onPressed: c.grantBusy
                    ? null
                    : () async {
                        if (await deskConfirm(context,
                            title: l.t('executive.grants.approve'),
                            text: l.f('executive.grants.row', {
                              'grantee': g.granteeUserId,
                              'scopes': _scopesText(l, g.scopes)
                            }),
                            extra: DeskHash(
                                label: l.t('executive.grants.hash'),
                                hash: g.payloadHash),
                            confirm: l.t('executive.grants.approve'),
                            cancel: l.t('executive.grants.cancel'),
                            confirmKey:
                                const Key('ex-grant-approve-confirm'))) {
                          await c.approve(g);
                        }
                      },
                child: Text(l.t('executive.grants.approve'))),
            OutlinedButton(
                key: Key('ex-grant-reject-${g.id}'),
                onPressed: c.grantBusy ? null : () => c.reject(g),
                child: Text(l.t('executive.grants.reject'))),
          ]),
        ],
        if (g.status == 'active') ...[
          const SizedBox(height: AnumSpacing.xs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            OutlinedButton(
                key: Key('ex-grant-narrow-${g.id}'),
                onPressed: c.grantBusy
                    ? null
                    : () => setState(() {
                          _narrowing = g.id;
                          _narrowScopes
                            ..clear()
                            ..addAll(g.scopes);
                        }),
                child: Text(l.t('executive.grants.narrow'))),
            OutlinedButton(
                key: Key('ex-grant-revoke-${g.id}'),
                style: OutlinedButton.styleFrom(foregroundColor: p.stop),
                onPressed: c.grantBusy
                    ? null
                    : () async {
                        if (await deskConfirm(context,
                            title: l.f('executive.grants.revokeConfirm',
                                {'grantee': g.granteeUserId}),
                            text: _scopesText(l, g.scopes),
                            confirm: l.t('executive.grants.revoke'),
                            cancel: l.t('executive.grants.cancel'),
                            danger: true,
                            confirmKey: const Key('ex-grant-revoke-confirm'))) {
                          await c.revoke(g);
                        }
                      },
                child: Text(l.t('executive.grants.revoke'))),
          ]),
          if (_narrowing == g.id) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(l.t('executive.grants.narrowNote'),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            _scopeBoxes(_narrowScopes, g.scopes, l, 'ex-narrow'),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton(
                      key: const Key('ex-narrow-save'),
                      onPressed: c.grantBusy ||
                              _narrowScopes.isEmpty ||
                              _narrowScopes.length == g.scopes.length
                          ? null
                          : () async {
                              if (await c.narrow(g, _ordered(_narrowScopes)) &&
                                  mounted) {
                                setState(() => _narrowing = null);
                              }
                            },
                      child: Text(l.t('executive.grants.narrowSave'))),
                  TextButton(
                      onPressed: () => setState(() => _narrowing = null),
                      child: Text(l.t('executive.grants.cancel'))),
                ]),
          ],
        ],
      ],
    );
  }
}
