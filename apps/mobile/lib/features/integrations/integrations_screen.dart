import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../workspace/list_kit.dart';
import 'integrations_controller.dart';
import 'integrations_models.dart';

/// A failure in words that say how to recover.
String describeIntegrationError(AnumLocalizations l, Object? error) =>
    switch (error) {
      ApiException(statusCode: 0) => l.t('intOffline'),
      ApiException(statusCode: 401) => l.t('intSignedOut'),
      ApiException(isPermissionDenied: true, :final message) =>
        '$message ${l.t('intOwnersOnly')}',
      ApiException(:final readable) => readable,
      null => l.t('intUnknownError'),
      _ => '$error',
    };

IconData integrationIcon(Integration i) => switch (i.kind) {
      'email' => Icons.mail_outline,
      'database' => Icons.storage_outlined,
      'identity' => Icons.badge_outlined,
      'event_bus' => Icons.hub_outlined,
      'workflow' => Icons.account_tree_outlined,
      'cache' => Icons.bolt_outlined,
      'object_storage' => Icons.cloud_outlined,
      'search' => Icons.travel_explore,
      _ => Icons.webhook_outlined,
    };

AnumTone integrationTone(Integration i) => switch (i.kind) {
      'email' => AnumTone.stop,
      'database' || 'cache' => AnumTone.info,
      'identity' => AnumTone.accent,
      'search' => AnumTone.ok,
      'tool' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String integrationKindKey(String kind) => switch (kind) {
      'email' => 'intKindEmail',
      'database' => 'intKindDatabase',
      'identity' => 'intKindIdentity',
      'event_bus' => 'intKindEvents',
      'workflow' => 'intKindWorkflow',
      'cache' => 'intKindCache',
      'object_storage' => 'intKindStorage',
      'search' => 'intKindSearch',
      _ => 'intKindTool',
    };

/// Integrations (mockup 12): what this workspace is connected to, from the
/// integrations API, with Connected / Connect on each row. Connecting does
/// the real thing for each kind: an owner turns a configured service on,
/// tests it and stores its key here; a mailbox is connected by each person
/// in the inbox; a service the server does not have yet says what the
/// operator must set.
class IntegrationsScreen extends StatefulWidget {
  const IntegrationsScreen(
      {required this.controller, this.onOpenInbox, super.key});
  final IntegrationsController controller;

  /// Opens the inbox, where each person connects Gmail, Outlook or Zoho
  /// Mail (docs/email.md). Without it the sheet says where to go.
  final VoidCallback? onOpenInbox;

  @override
  State<IntegrationsScreen> createState() => _IntegrationsScreenState();
}

class _IntegrationsScreenState extends State<IntegrationsScreen> {
  late final _search = TextEditingController(text: widget.controller.query);

  @override
  void initState() {
    super.initState();
    widget.controller.start();
  }

  @override
  void didUpdateWidget(IntegrationsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Another workspace's controller: load it too.
    if (oldWidget.controller != widget.controller) widget.controller.start();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _open(Integration integration) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      builder: (_) => IntegrationSheet(
          controller: widget.controller,
          integrationId: integration.id,
          onOpenInbox: widget.onOpenInbox));

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final l = AnumLocalizations.maybeOf(context);
          final p = context.palette;
          return AnumPage(
            onRefresh: c.refresh,
            children: [
              AnumListHeader(
                  title: l.t('intTitle'), subtitle: l.t('intSubtitle')),
              AnumSegmentTabs<IntegrationTab>(
                segments: [
                  AnumSegment(IntegrationTab.all, l.t('listTabAll'),
                      count: c.phase == IntegrationsPhase.ready
                          ? c.count(IntegrationTab.all)
                          : null,
                      key: const Key('int-tab-all')),
                  AnumSegment(IntegrationTab.connected, l.t('intTabConnected'),
                      count: c.phase == IntegrationsPhase.ready
                          ? c.count(IntegrationTab.connected)
                          : null,
                      key: const Key('int-tab-connected')),
                  AnumSegment(IntegrationTab.available, l.t('intTabAvailable'),
                      count: c.phase == IntegrationsPhase.ready
                          ? c.count(IntegrationTab.available)
                          : null,
                      key: const Key('int-tab-available')),
                ],
                selected: c.tab,
                onSelected: c.select,
              ),
              if (c.items.length > 6)
                TextField(
                  key: const Key('int-search'),
                  controller: _search,
                  onChanged: c.search,
                  textInputAction: TextInputAction.search,
                  decoration: InputDecoration(
                      prefixIcon: const Icon(Icons.search),
                      hintText: l.t('intSearch')),
                ),
              ..._body(context, c, l, p),
            ],
          );
        },
      );

  List<Widget> _body(BuildContext context, IntegrationsController c,
      AnumLocalizations l, AnumPalette p) {
    switch (c.phase) {
      case IntegrationsPhase.loading:
        return [AnumListLoading(label: l.t('intLoading'))];
      case IntegrationsPhase.denied:
        return [
          AnumSurface(
            accent: p.warn,
            child: AnumEmpty(
                icon: Icons.lock_outline,
                title: l.t('intDenied'),
                message: l.t('intDeniedHint')),
          )
        ];
      case IntegrationsPhase.error:
        return [
          AnumSurface(
            key: const Key('int-error'),
            accent: p.stop,
            child: AnumEmpty(
                icon: Icons.cloud_off_outlined,
                title: l.t('intLoadFailed'),
                message: describeIntegrationError(l, c.error),
                action: l.t('retry'),
                onAction: c.refresh),
          )
        ];
      case IntegrationsPhase.ready:
        break;
    }
    final rows = c.visible;
    if (rows.isEmpty) {
      final filtered = c.query.trim().isNotEmpty;
      return [
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.extension_outlined,
            title: c.items.isEmpty
                ? l.t('intEmpty')
                : filtered
                    ? l.t('intNoMatch')
                    : c.tab == IntegrationTab.connected
                        ? l.t('intNoneConnected')
                        : l.t('intAllConnected'),
            message: c.items.isEmpty
                ? l.t('intEmptyHint')
                : filtered
                    ? l.t('intNoMatchHint')
                    : c.tab == IntegrationTab.connected
                        ? l.t('intNoneConnectedHint')
                        : l.t('intAllConnectedHint'),
            action: c.items.isEmpty
                ? l.t('retry')
                : c.tab == IntegrationTab.connected && !filtered
                    ? l.t('intShowAvailable')
                    : l.t('tasksClearFilters'),
            onAction: c.items.isEmpty
                ? c.refresh
                : c.tab == IntegrationTab.connected && !filtered
                    ? () => c.select(IntegrationTab.available)
                    : () {
                        _search.clear();
                        c.clearFilters();
                      },
          ),
        ),
      ];
    }
    return [
      AnumListCard(children: [
        for (final integration in rows)
          _IntegrationRow(
              integration: integration,
              owner: c.isOwner,
              busy: c.busy.contains(integration.id),
              onOpen: () => _open(integration),
              onConnectMailbox: widget.onOpenInbox),
      ]),
    ];
  }
}

class _IntegrationRow extends StatelessWidget {
  const _IntegrationRow(
      {required this.integration,
      required this.owner,
      required this.busy,
      required this.onOpen,
      this.onConnectMailbox});
  final Integration integration;
  final bool owner, busy;
  final VoidCallback onOpen;
  final VoidCallback? onConnectMailbox;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final action = integrationAction(integration, owner: owner);
    final state = l.t(actionKey(action));
    final Widget trailing = switch (action) {
      IntegrationAction.connect => FilledButton.tonal(
          key: Key('int-connect-${integration.id}'),
          onPressed: busy ? null : onOpen,
          child: Text(l.t('intConnect'))),
      IntegrationAction.connectMailbox => FilledButton.tonal(
          key: Key('int-connect-${integration.id}'),
          onPressed: onConnectMailbox ?? onOpen,
          child: Text(l.t('intConnect'))),
      _ => AnumPill(label: state, tone: actionTone(action)),
    };
    return AnumListRow(
      key: Key('int-row-${integration.id}'),
      leading: AnumTileIcon(
          icon: integrationIcon(integration),
          color: anumToneColor(context, integrationTone(integration))),
      title: integration.name,
      subtitle: l.t(integrationKindKey(integration.kind)),
      trailing: trailing,
      semanticLabel: '${integration.name}, $state',
      onTap: onOpen,
    );
  }
}

String actionKey(IntegrationAction action) => switch (action) {
      IntegrationAction.connected => 'intConnected',
      IntegrationAction.attention => 'intAttention',
      IntegrationAction.connect => 'intNotConnected',
      IntegrationAction.connectMailbox => 'intNotConnected',
      IntegrationAction.operator => 'intServerSetup',
      IntegrationAction.ownerOnly => 'intOwnerTurnsOn',
    };

AnumTone actionTone(IntegrationAction action) => switch (action) {
      IntegrationAction.connected => AnumTone.ok,
      IntegrationAction.attention => AnumTone.warn,
      _ => AnumTone.neutral,
    };

/// One integration: its health, what it uses, and what this person can do.
class IntegrationSheet extends StatefulWidget {
  const IntegrationSheet(
      {required this.controller,
      required this.integrationId,
      this.onOpenInbox,
      super.key});
  final IntegrationsController controller;
  final String integrationId;
  final VoidCallback? onOpenInbox;

  @override
  State<IntegrationSheet> createState() => _IntegrationSheetState();
}

class _IntegrationSheetState extends State<IntegrationSheet> {
  final _secret = TextEditingController();
  String? _secretError, _result;
  bool _resultFailed = false;

  @override
  void dispose() {
    _secret.dispose();
    super.dispose();
  }

  Future<void> _run(Future<Object?> Function() action, String done) async {
    final l = AnumLocalizations.maybeOf(context);
    setState(() => _result = null);
    final failure = await action();
    if (!mounted) return;
    setState(() {
      _resultFailed = failure != null;
      _result = failure == null ? done : describeIntegrationError(l, failure);
    });
  }

  Future<void> _storeSecret(Integration i) async {
    final l = AnumLocalizations.maybeOf(context);
    final problem = integrationSecretProblem(_secret.text.trim());
    if (problem != null) {
      setState(() => _secretError = l.t('intSecret_$problem'));
      return;
    }
    setState(() => _secretError = null);
    final secret = _secret.text.trim();
    await _run(() => widget.controller.storeCredential(i.id, secret),
        l.t('intSecretSaved'));
    if (!_resultFailed) _secret.clear();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final i = c.byId(widget.integrationId);
        final l = AnumLocalizations.maybeOf(context);
        final p = context.palette;
        final text = Theme.of(context).textTheme;
        if (i == null) {
          return Padding(
            padding: const EdgeInsets.all(AnumSpacing.lg),
            child: AnumEmpty(
                icon: Icons.extension_off_outlined,
                title: l.t('intGone'),
                message: l.t('intGoneHint')),
          );
        }
        final action = integrationAction(i, owner: c.isOwner);
        final busy = c.busy.contains(i.id);
        return SingleChildScrollView(
          padding: EdgeInsets.fromLTRB(AnumSpacing.lg, 0, AnumSpacing.lg,
              AnumSpacing.lg + MediaQuery.viewInsetsOf(context).bottom),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 640),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(children: [
                      AnumTileIcon(
                          icon: integrationIcon(i),
                          color: anumToneColor(context, integrationTone(i))),
                      const SizedBox(width: AnumSpacing.sm),
                      Expanded(
                        child: Semantics(
                          header: true,
                          child: Text(i.name,
                              style: text.titleLarge?.copyWith(
                                  color: p.text, fontWeight: FontWeight.w800)),
                        ),
                      ),
                    ]),
                    const SizedBox(height: AnumSpacing.sm),
                    Wrap(
                        spacing: AnumSpacing.xs,
                        runSpacing: AnumSpacing.xs,
                        children: [
                          AnumPill(
                              label: l.t(actionKey(action)),
                              tone: actionTone(action)),
                          AnumPill(label: l.t(integrationKindKey(i.kind))),
                        ]),
                    const SizedBox(height: AnumSpacing.sm),
                    Text(i.detail,
                        style: text.bodyMedium?.copyWith(color: p.muted)),
                    const SizedBox(height: AnumSpacing.md),
                    _Facts(integration: i),
                    if (i.lastError != null) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      Text(
                          l.f('intLastError', {
                            'error': i.lastError!,
                            'when': i.lastErrorAt == null
                                ? ''
                                : relativeTime(i.lastErrorAt!),
                          }),
                          style: text.bodySmall?.copyWith(color: p.stop)),
                    ],
                    if (i.remediation != null &&
                        (i.degraded || !i.connected)) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      Text(l.f('intHowToFix', {'fix': i.remediation!}),
                          style: text.bodySmall?.copyWith(color: p.muted)),
                    ],
                    const SizedBox(height: AnumSpacing.md),
                    ..._actions(context, c, i, action, busy, l, p),
                    if (_result != null) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      Semantics(
                        liveRegion: true,
                        child: Text(_result!,
                            key: const Key('int-result'),
                            style: text.bodyMedium?.copyWith(
                                color: _resultFailed ? p.stop : p.ok)),
                      ),
                    ],
                  ]),
            ),
          ),
        );
      });

  List<Widget> _actions(
      BuildContext context,
      IntegrationsController c,
      Integration i,
      IntegrationAction action,
      bool busy,
      AnumLocalizations l,
      AnumPalette p) {
    final note = TextStyle(color: p.muted, fontSize: AnumType.smPlus);
    if (i.isEmail) {
      return [
        Text(l.t('intMailboxHint'), style: note),
        if (i.available && widget.onOpenInbox != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          FilledButton.icon(
              key: const Key('int-open-inbox'),
              onPressed: () {
                Navigator.pop(context);
                widget.onOpenInbox!();
              },
              icon: const Icon(Icons.mail_outline),
              label: Text(l.t('intOpenInbox'))),
        ],
        if (!i.available) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t('intOperatorHint'), style: note),
        ],
      ];
    }
    if (action == IntegrationAction.operator) {
      return [
        Text(
            i.environmentVariable == null
                ? l.t('intOperatorHint')
                : l.f('intOperatorEnv', {'env': i.environmentVariable!}),
            key: const Key('int-operator'),
            style: note),
      ];
    }
    if (!c.isOwner) {
      return [
        Text(l.t('intOwnersOnly'),
            key: const Key('int-owner-only'), style: note)
      ];
    }
    return [
      SwitchListTile(
        key: const Key('int-enabled'),
        contentPadding: EdgeInsets.zero,
        title: Text(l.t('intUseHere')),
        subtitle: Text(i.enabled ? l.t('intUseHereOn') : l.t('intUseHereOff')),
        value: i.enabled,
        onChanged: busy
            ? null
            : (value) => _run(() => c.setEnabled(i.id, value),
                value ? l.t('intTurnedOn') : l.t('intTurnedOff')),
      ),
      Align(
        alignment: AlignmentDirectional.centerStart,
        child: OutlinedButton.icon(
          key: const Key('int-test'),
          onPressed: busy || !i.enabled
              ? null
              : () => _run(() => c.test(i.id), l.t('intTested')),
          icon: busy
              ? const SizedBox.square(
                  dimension: AnumSpacing.md,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.network_check),
          label: Text(l.t('intTest')),
        ),
      ),
      if (i.acceptsWorkspaceCredential) ...[
        const SizedBox(height: AnumSpacing.md),
        Text(l.t('intKeyTitle'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        const SizedBox(height: AnumSpacing.xxs),
        Text(
            i.hasWorkspaceCredential
                ? l.f('intKeyStored', {'hint': i.workspaceCredentialHint!})
                : l.t('intKeyNone'),
            style: note),
        const SizedBox(height: AnumSpacing.xs),
        TextField(
          key: const Key('int-secret'),
          controller: _secret,
          obscureText: true,
          enableSuggestions: false,
          autocorrect: false,
          decoration: InputDecoration(
              labelText: l.t('intKeyLabel'),
              helperText: l.t('intKeyHelp'),
              helperMaxLines: 3,
              errorText: _secretError),
        ),
        const SizedBox(height: AnumSpacing.xs),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton(
              key: const Key('int-secret-save'),
              onPressed: busy ? null : () => _storeSecret(i),
              child: Text(l.t('intKeySave'))),
          if (i.hasWorkspaceCredential)
            TextButton(
                key: const Key('int-secret-remove'),
                onPressed: busy
                    ? null
                    : () => _run(
                        () => c.removeCredential(i.id), l.t('intKeyRemoved')),
                child: Text(l.t('intKeyRemove'))),
        ]),
      ],
    ];
  }
}

class _Facts extends StatelessWidget {
  const _Facts({required this.integration});
  final Integration integration;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final i = integration;
    Widget fact(String label, String value) => Padding(
          padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(label,
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            const SizedBox(height: 2),
            SelectableText(value,
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.smPlus,
                    fontWeight: FontWeight.w600)),
          ]),
        );
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (i.endpoint.isNotEmpty) fact(l.t('intEndpoint'), i.endpoint),
      fact(
          l.t('intChecked'),
          i.checkedAt == null
              ? l.t('intNeverChecked')
              : [
                  relativeTime(i.checkedAt!),
                  if (i.latencyMs != null) '${i.latencyMs} ms',
                ].join(' · ')),
      fact(l.t('intCredential'), l.t('intSource_${i.credentialSource}')),
      if (i.scopes.isNotEmpty) fact(l.t('intScopes'), i.scopes.join(', ')),
    ]);
  }
}
