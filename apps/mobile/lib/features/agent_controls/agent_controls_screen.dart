import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../admin/admin_models.dart'
    show formatResetDate, formatTokens, formatUsd;
import 'agent_controls_data.dart';

/// The text of a [ControlsNotice] in the page language.
String noticeText(AnumLocalizations l, ControlsNotice notice) {
  if (notice.raw != null) return notice.raw!;
  final key = notice.key ?? '';
  if (notice.count != null) return l.p(key, notice.count!, notice.values);
  return notice.values.isEmpty ? l.t(key) : l.f(key, notice.values);
}

String heldKindName(AnumLocalizations l, String kind) => switch (kind) {
      'mail' => l.t('ac.held.mail'),
      'post' => l.t('ac.held.post'),
      _ => l.t('ac.held.approval'),
    };

/// Agent controls (docs/agent-controls.md): the organization's emergency stop
/// for every agent and outbound send, what it holds, and monthly cost and
/// token caps per agent (the default assistant included).
class AgentControlsScreen extends StatefulWidget {
  const AgentControlsScreen({required this.controller, super.key});
  final AgentControlsController controller;
  @override
  State<AgentControlsScreen> createState() => _AgentControlsScreenState();
}

class _AgentControlsScreenState extends State<AgentControlsScreen> {
  final _reason = TextEditingController();
  final _search = TextEditingController();
  AgentControlsController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  void dispose() {
    _reason.dispose();
    _search.dispose();
    super.dispose();
  }

  Future<void> _confirmToggle(EmergencyStopState stop) async {
    final l = AnumLocalizations.of(context);
    if (parseStopReason(_reason.text).value == null) {
      await c.toggle(_reason.text); // shows what is wrong with the reason
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title:
            Text(l.t(stop.engaged ? 'ac.confirmRelease' : 'ac.confirmEngage')),
        content: Text(l.t(
            stop.engaged ? 'ac.confirmReleaseText' : 'ac.confirmEngageText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(l.t('ac.cancel'))),
          FilledButton(
              key: const Key('confirm-stop-change'),
              onPressed: () => Navigator.pop(context, true),
              child: Text(l.t(stop.engaged ? 'ac.release' : 'ac.engage'))),
        ],
      ),
    );
    if (ok != true) return;
    if (await c.toggle(_reason.text)) _reason.clear();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final p = context.palette;
          final l = AnumLocalizations.of(context);
          return Scaffold(
            appBar: AppBar(title: Text(l.t('ac.title')), actions: [
              IconButton(
                  tooltip: l.t('ac.refresh'),
                  onPressed: c.changing ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                eyebrow: l.t('ac.eyebrow'),
                title: l.t('ac.title'),
                subtitle: l.t('ac.intro'),
                large: true,
              ),
              ...switch (c.phase) {
                AgentControlsPhase.loading => [
                    const Padding(
                        padding: EdgeInsets.all(AnumSpacing.lg),
                        child: Center(child: CircularProgressIndicator()))
                  ],
                AgentControlsPhase.error => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                          icon: Icons.error_outline,
                          title: l.t('ac.loadFailed'),
                          message: l.f(
                              'ac.loadFailedHint', {'message': c.error ?? '-'}),
                          action: l.t('ac.retry'),
                          onAction: c.load,
                        )),
                  ],
                AgentControlsPhase.ready => [_stopCard(context, c.stop!)],
              },
              if (c.heldPhase != HeldPhase.idle &&
                  c.heldPhase != HeldPhase.forbidden)
                _heldCard(context),
              _capsCard(context),
            ]),
          );
        },
      );

  Widget _stopCard(BuildContext context, EmergencyStopState stop) {
    final p = context.palette;
    final l = AnumLocalizations.of(context);
    final notice = c.stopNotice;
    final noticeWidget = notice == null
        ? null
        : Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: Text(noticeText(l, notice),
                key: const Key('stop-notice'),
                style: TextStyle(color: notice.failed ? p.stop : p.ok)),
          );
    return AnumSurface(
      key: const Key('emergency-stop'),
      accent: stop.engaged ? p.stop : null,
      semanticLabel: l.t('ac.stopLabel'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          title: l.t('ac.stopTitle'),
          subtitle: l.t(!stop.engaged
              ? 'ac.offSummary'
              : stop.canRelease
                  ? 'ac.onRelease'
                  : 'ac.onWait'),
          trailing: AnumPill(
              label: l.t(stop.engaged ? 'ac.on' : 'ac.off'),
              tone: stop.engaged ? AnumTone.stop : AnumTone.ok),
        ),
        const SizedBox(height: AnumSpacing.sm),
        for (final key in const [
          'ac.stops.models',
          'ac.stops.tools',
          'ac.stops.approvals',
          'ac.stops.automations',
          'ac.stops.running',
        ])
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('•  ', style: TextStyle(color: p.muted)),
              Expanded(
                  child: Text(l.t(key),
                      style: TextStyle(color: p.muted, fontSize: 13))),
            ]),
          ),
        if (stop.changedAt != null) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(
              l.f(stop.engaged ? 'ac.changedOn' : 'ac.changedOff', {
                'user':
                    stop.changedByYou ? l.t('ac.you') : stop.changedBy ?? '-',
                'when': MaterialLocalizations.of(context)
                    .formatMediumDate(stop.changedAt!.toLocal()),
              }),
              style: TextStyle(color: p.faint, fontSize: 12)),
        ],
        if (stop.reason != null)
          Text(l.f('ac.reasonShown', {'reason': stop.reason!}),
              style: TextStyle(color: p.text)),
        Divider(height: 28, color: p.line),
        if (stop.canChange) ...[
          TextField(
              key: const Key('stop-reason'),
              controller: _reason,
              maxLength: 300,
              minLines: 2,
              maxLines: 4,
              decoration: InputDecoration(
                  labelText: l.t('ac.reason'),
                  hintText: l.t(
                      stop.engaged ? 'ac.reasonOffHint' : 'ac.reasonOnHint'))),
          if (noticeWidget != null) noticeWidget,
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('stop-toggle'),
              style: stop.engaged
                  ? null
                  : FilledButton.styleFrom(backgroundColor: p.stop),
              onPressed: c.changing ? null : () => _confirmToggle(stop),
              icon: Icon(stop.engaged ? Icons.play_arrow : Icons.block),
              label: Text(c.changing
                  ? l.t('ac.saving')
                  : l.t(stop.engaged ? 'ac.release' : 'ac.engage')),
            ),
          ),
        ] else ...[
          if (noticeWidget != null) noticeWidget,
          Row(
              key: const Key('stop-read-only'),
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.lock_outline, size: 18, color: p.muted),
                const SizedBox(width: AnumSpacing.sm),
                Expanded(
                    child: Text(
                        l.t(stop.engaged
                            ? 'ac.cannotRelease'
                            : 'ac.cannotEngage'),
                        style: TextStyle(color: p.muted, fontSize: 13))),
              ]),
        ],
      ]),
    );
  }

  Widget _heldCard(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.of(context);
    final page = c.held;
    return AnumSurface(
      key: const Key('held'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(title: l.t('ac.held.title'), subtitle: l.t('ac.held.intro')),
        const SizedBox(height: AnumSpacing.sm),
        if (c.heldPhase == HeldPhase.loading && page == null)
          const Center(child: CircularProgressIndicator())
        else if (c.heldPhase == HeldPhase.error)
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('ac.held.failed'),
              message: c.heldError ?? '-',
              action: l.t('ac.retry'),
              onAction: c.loadHeld)
        else if (page != null && page.total == 0)
          AnumEmpty(
              icon: Icons.verified_outlined,
              title: l.t('ac.held.empty'),
              message: l.t('ac.held.emptyHint'))
        else if (page != null) ...[
          for (final item in page.items)
            Padding(
              key: Key('held-${item.kind}-${item.id}'),
              padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    AnumPill(label: heldKindName(l, item.kind)),
                    const SizedBox(height: AnumSpacing.xxs),
                    Text(item.title,
                        style: TextStyle(
                            color: p.text, fontWeight: FontWeight.w700)),
                    if (item.detail.isNotEmpty)
                      Text(item.detail,
                          style: TextStyle(color: p.muted, fontSize: 12.5)),
                    if (item.at != null)
                      Text(
                          MaterialLocalizations.of(context)
                              .formatMediumDate(item.at!.toLocal()),
                          style: TextStyle(color: p.faint, fontSize: 12)),
                  ]),
            ),
          if (page.total > AgentControlsController.pageSize)
            _Pager(
                offset: c.heldOffset,
                shown: page.items.length,
                total: page.total,
                onPage: c.heldPage),
        ],
      ]),
    );
  }

  Widget _capsCard(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.of(context);
    final page = c.caps;
    return AnumSurface(
      key: const Key('agent-caps'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          title: l.t('ac.capsTitle'),
          subtitle: page == null || page.resetsOn.isEmpty
              ? l.t('ac.capsIntro')
              : l.f('ac.capsResets', {'date': formatResetDate(page.resetsOn)}),
        ),
        const SizedBox(height: AnumSpacing.sm),
        if (c.capsPhase == CapsPhase.forbidden)
          AnumEmpty(
            key: const Key('caps-forbidden'),
            icon: Icons.lock_outline,
            title: l.t('ac.capsForbidden'),
            message: l.t('ac.capsForbiddenHint'),
          )
        else ...[
          TextField(
            key: const Key('caps-search'),
            controller: _search,
            textInputAction: TextInputAction.search,
            onSubmitted: c.search,
            decoration: InputDecoration(
                labelText: l.t('ac.search'),
                prefixIcon: const Icon(Icons.search),
                suffixIcon: IconButton(
                    tooltip: l.t('ac.searchGo'),
                    onPressed: () => c.search(_search.text),
                    icon: const Icon(Icons.arrow_forward))),
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (c.capsPhase == CapsPhase.loading && page == null)
            const Center(child: CircularProgressIndicator())
          else if (c.capsPhase == CapsPhase.error)
            AnumEmpty(
                icon: Icons.error_outline,
                title: l.t('ac.capsFailed'),
                message: c.capsError ?? '-',
                action: l.t('ac.retry'),
                onAction: c.loadCaps)
          else if (page != null && page.total == 0)
            c.query.trim().isEmpty
                ? AnumEmpty(
                    icon: Icons.smart_toy_outlined,
                    title: l.t('ac.noAgents'),
                    message: l.t('ac.noAgentsHint'))
                : AnumEmpty(
                    icon: Icons.search_off,
                    title: l.t('ac.noMatch'),
                    message: l.t('ac.noMatchHint'),
                    action: l.t('ac.clearSearch'),
                    onAction: () {
                      _search.clear();
                      c.search('');
                    })
          else if (page != null) ...[
            if (!page.canManage)
              Text(l.t('ac.readOnly'),
                  style: TextStyle(color: p.muted, fontSize: 13)),
            for (final item in page.items)
              _CapTile(
                  key: Key('cap-${item.agentId}'),
                  item: item,
                  controller: c,
                  canManage: page.canManage),
            if (page.total > AgentControlsController.pageSize)
              _Pager(
                  offset: c.offset,
                  shown: page.items.length,
                  total: page.total,
                  onPage: c.page),
          ],
        ],
      ]),
    );
  }
}

class _Pager extends StatelessWidget {
  const _Pager(
      {required this.offset,
      required this.shown,
      required this.total,
      required this.onPage});
  final int offset, shown, total;
  final ValueChanged<int> onPage;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    const size = AgentControlsController.pageSize;
    return Wrap(
        spacing: 8,
        runSpacing: 8,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(l.f('ac.range',
              {'from': offset + 1, 'to': offset + shown, 'total': total})),
          OutlinedButton(
              onPressed: offset == 0 ? null : () => onPage(offset - size),
              child: Text(l.t('ac.previous'))),
          OutlinedButton(
              onPressed:
                  offset + size >= total ? null : () => onPage(offset + size),
              child: Text(l.t('ac.next'))),
        ]);
  }
}

class _CapTile extends StatefulWidget {
  const _CapTile(
      {required this.item,
      required this.controller,
      required this.canManage,
      super.key});
  final AgentCapItem item;
  final AgentControlsController controller;
  final bool canManage;
  @override
  State<_CapTile> createState() => _CapTileState();
}

class _CapTileState extends State<_CapTile> {
  late final cost = TextEditingController(text: _usd(widget.item.limitUsd));
  late final tokens =
      TextEditingController(text: widget.item.tokenLimit?.toString() ?? '');

  static String _usd(double? value) => value == null
      ? ''
      : (value == value.roundToDouble() ? value.toStringAsFixed(0) : '$value');

  @override
  void didUpdateWidget(covariant _CapTile old) {
    super.didUpdateWidget(old);
    if (old.item.limitUsd != widget.item.limitUsd) {
      cost.text = _usd(widget.item.limitUsd);
    }
    if (old.item.tokenLimit != widget.item.tokenLimit) {
      tokens.text = widget.item.tokenLimit?.toString() ?? '';
    }
  }

  @override
  void dispose() {
    cost.dispose();
    tokens.dispose();
    super.dispose();
  }

  Widget _bar(BuildContext context, double? fraction, int? percent) {
    final p = context.palette;
    final color = (percent ?? 0) >= 100
        ? p.stop
        : (percent ?? 0) >= 80
            ? p.warn
            : p.sky;
    return ClipRRect(
      borderRadius: BorderRadius.circular(99),
      child: LinearProgressIndicator(
          value: fraction ?? 0,
          minHeight: 8,
          color: color,
          backgroundColor: p.background),
    );
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.of(context);
    final item = widget.item;
    final c = widget.controller;
    final percent = item.percent;
    final notice = c.capNotices[item.agentId];
    final limit =
        item.limitUsd == null ? l.t('ac.noCap') : formatUsd(item.limitUsd!);
    return Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.md),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(item.name,
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
              if (item.isDefault) AnumPill(label: l.t('ac.default')),
              if (item.sharing == 'private') AnumPill(label: l.t('ac.private')),
              if (item.exceeded)
                AnumPill(label: l.t('ac.capReached'), tone: AnumTone.stop)
              else if (item.worstPercent >= 80)
                AnumPill(label: l.t('ac.capNear'), tone: AnumTone.warn),
            ]),
        if (item.isDefault)
          Text(l.t('ac.defaultHint'),
              style: TextStyle(color: p.faint, fontSize: 12)),
        const SizedBox(height: 6),
        Semantics(
          label: l.f('ac.meter', {'name': item.name}),
          value: percent == null
              ? l.t('ac.noCap')
              : l.f('ac.meterValue', {
                  'used': formatUsd(item.costUsd),
                  'limit': limit,
                  'percent': percent
                }),
          excludeSemantics: true,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text('${formatUsd(item.costUsd)} / $limit',
                style: TextStyle(color: p.text, fontWeight: FontWeight.w800)),
            const SizedBox(height: 4),
            _bar(context, item.fraction, percent),
            if (item.tokenLimit != null) ...[
              const SizedBox(height: 6),
              Text(
                  l.f('ac.tokensUsed', {
                    'used': formatTokens(item.tokens),
                    'limit': formatTokens(item.tokenLimit!)
                  }),
                  style: TextStyle(color: p.text, fontSize: 12.5)),
              const SizedBox(height: 4),
              _bar(context, item.tokenFraction, item.tokenPercent),
            ],
            const SizedBox(height: 4),
            Text(
                '${l.p('ac.calls', item.calls)}${percent == null ? '' : ', ${l.f('ac.percentOfCap', {
                        'percent': percent
                      })}'}',
                style: TextStyle(color: p.faint, fontSize: 12)),
          ]),
        ),
        if (widget.canManage) ...[
          const SizedBox(height: AnumSpacing.xs),
          TextField(
            key: Key('cap-input-${item.agentId}'),
            controller: cost,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
                labelText: l.f('ac.costCap', {'name': item.name}),
                hintText: l.t('ac.noCap')),
          ),
          const SizedBox(height: AnumSpacing.xs),
          TextField(
            key: Key('token-input-${item.agentId}'),
            controller: tokens,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
                labelText: l.f('ac.tokenCap', {'name': item.name}),
                hintText: l.t('ac.noCap')),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton(
                key: Key('cap-save-${item.agentId}'),
                onPressed: c.savingAgent != null
                    ? null
                    : () => c.saveCap(item, cost.text, tokens.text),
                child: Text(c.savingAgent == item.agentId
                    ? l.t('ac.saving')
                    : l.t('ac.save'))),
          ),
          if (notice != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(noticeText(l, notice),
                  style: TextStyle(
                      color: notice.failed ? p.stop : p.ok, fontSize: 12.5)),
            ),
        ],
        Divider(height: 24, color: p.line),
      ]),
    );
  }
}
