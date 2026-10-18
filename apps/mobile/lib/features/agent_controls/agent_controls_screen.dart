import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../admin/admin_models.dart' show formatResetDate, formatUsd;
import 'agent_controls_data.dart';

/// Agent controls (docs/agent-controls.md): the organization's emergency stop
/// for every agent and outbound send, and a monthly cost cap per agent.
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
    final parsed = parseStopReason(_reason.text);
    if (parsed.value == null) {
      await c.toggle(_reason.text); // shows the reason problem
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(stop.engaged
            ? 'Turn the emergency stop off?'
            : 'Stop all agents and sends?'),
        content: Text(stop.engaged
            ? 'Agents and automations start working again, and pending sends can be approved.'
            : 'Every agent, automation and outbound send in the organization pauses now, and running tasks are cancelled. Only the organization owner can turn it off.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel')),
          FilledButton(
              key: const Key('confirm-stop-change'),
              onPressed: () => Navigator.pop(context, true),
              child: Text(stop.engaged
                  ? 'Turn the stop off'
                  : 'Stop all agents and sends')),
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
          return Scaffold(
            appBar: AppBar(title: const Text('Agent controls'), actions: [
              IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.changing ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              const AnumHeader(
                eyebrow: 'Safety',
                title: 'Agent controls',
                subtitle:
                    'Stop every agent and outbound send at once, and cap what each agent may spend on the model each month.',
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
                          title: 'Agent controls could not be loaded',
                          message:
                              '${c.error ?? 'Request failed'}. Check your connection, then try again.',
                          action: 'Retry',
                          onAction: c.load,
                        )),
                  ],
                AgentControlsPhase.ready => [_stopCard(context, c.stop!)],
              },
              _capsCard(context),
            ]),
          );
        },
      );

  Widget _stopCard(BuildContext context, EmergencyStopState stop) {
    final p = context.palette;
    return AnumSurface(
      key: const Key('emergency-stop'),
      accent: stop.engaged ? p.stop : null,
      semanticLabel: 'Emergency stop for all agents and sends',
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          title: 'Emergency stop',
          subtitle: !stop.engaged
              ? 'Agents, automations and outbound sends run as usual. Turn the stop on if an agent misbehaves.'
              : stop.canRelease
                  ? 'Everything is paused for the whole organization. Pending sends stay pending.'
                  : 'Everything is paused for the whole organization. Only the organization owner can turn it off.',
          trailing: AnumPill(
              label: stop.engaged ? 'On: paused' : 'Off: running',
              tone: stop.engaged ? AnumTone.stop : AnumTone.ok),
        ),
        const SizedBox(height: AnumSpacing.sm),
        for (final line in const [
          'No agent, chat or voice answer calls the model.',
          'No tool runs, so nothing is sent by email, WhatsApp, Brevo, phone or social posts.',
          'Pending sends cannot be approved; they wait, and can still be rejected.',
          'Scheduled and triggered automations do not start.',
          'Running and queued tasks are cancelled when it is turned on.',
        ])
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('•  ', style: TextStyle(color: p.muted)),
              Expanded(
                  child: Text(line,
                      style: TextStyle(color: p.muted, fontSize: 13))),
            ]),
          ),
        if (stop.changedAt != null) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(
              'Turned ${stop.engaged ? 'on' : 'off'} by ${stop.changedByYou ? 'you' : stop.changedBy ?? '-'} on ${MaterialLocalizations.of(context).formatMediumDate(stop.changedAt!.toLocal())}.',
              style: TextStyle(color: p.faint, fontSize: 12)),
        ],
        if (stop.reason != null)
          Text('Reason: ${stop.reason}', style: TextStyle(color: p.text)),
        Divider(height: 28, color: p.line),
        if (stop.canChange) ...[
          TextField(
              key: const Key('stop-reason'),
              controller: _reason,
              maxLength: 300,
              minLines: 2,
              maxLines: 4,
              decoration: InputDecoration(
                  labelText: 'Reason (kept in the audit log)',
                  hintText: stop.engaged
                      ? 'For example: fixed the outreach agent'
                      : 'For example: the outreach agent is sending duplicates')),
          if (c.stopNotice != null)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: Text(c.stopNotice!,
                  key: const Key('stop-notice'),
                  style: TextStyle(color: c.stopNoticeFailed ? p.stop : p.ok)),
            ),
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
                  ? 'Saving...'
                  : stop.engaged
                      ? 'Turn the stop off'
                      : 'Stop all agents and sends'),
            ),
          ),
        ] else ...[
          if (c.stopNotice != null)
            Text(c.stopNotice!,
                key: const Key('stop-notice'),
                style: TextStyle(color: c.stopNoticeFailed ? p.stop : p.ok)),
          Row(
              key: const Key('stop-read-only'),
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.lock_outline, size: 18, color: p.muted),
                const SizedBox(width: AnumSpacing.sm),
                Expanded(
                    child: Text(
                        stop.engaged
                            ? 'Only the organization owner (organization administrator) can turn the stop off.'
                            : 'Only a workspace owner or the organization administrator can turn the stop on.',
                        style: TextStyle(color: p.muted, fontSize: 13))),
              ]),
        ],
      ]),
    );
  }

  Widget _capsCard(BuildContext context) {
    final p = context.palette;
    final page = c.caps;
    return AnumSurface(
      key: const Key('agent-caps'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          title: 'Monthly cost caps per agent',
          subtitle: page == null || page.resetsOn.isEmpty
              ? 'Each agent stops calling the model once its estimated cost this month reaches its cap.'
              : 'Each agent stops calling the model once its estimated cost this month reaches its cap. Resets on ${formatResetDate(page.resetsOn)}.',
        ),
        const SizedBox(height: AnumSpacing.sm),
        if (c.capsPhase == CapsPhase.forbidden)
          const AnumEmpty(
            key: Key('caps-forbidden'),
            icon: Icons.lock_outline,
            title: 'Agent caps are for owners',
            message:
                'Only a workspace owner or the organization administrator can see and change agent cost caps.',
          )
        else ...[
          TextField(
            key: const Key('caps-search'),
            controller: _search,
            textInputAction: TextInputAction.search,
            onSubmitted: c.search,
            decoration: InputDecoration(
                labelText: 'Search agents',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: IconButton(
                    tooltip: 'Search',
                    onPressed: () => c.search(_search.text),
                    icon: const Icon(Icons.arrow_forward))),
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (c.capsPhase == CapsPhase.loading && page == null)
            const Center(child: CircularProgressIndicator())
          else if (c.capsPhase == CapsPhase.error)
            AnumEmpty(
                icon: Icons.error_outline,
                title: "Couldn't load agent caps",
                message: c.capsError ?? 'Try again.',
                action: 'Retry',
                onAction: c.loadCaps)
          else if (page != null && page.total == 0)
            c.query.trim().isEmpty
                ? const AnumEmpty(
                    icon: Icons.smart_toy_outlined,
                    title: 'No agents yet',
                    message:
                        'Create an agent under Agents first; its cap and usage then show here.')
                : AnumEmpty(
                    icon: Icons.search_off,
                    title: 'No agent matches',
                    message: 'Try another name, or clear the search.',
                    action: 'Clear search',
                    onAction: () {
                      _search.clear();
                      c.search('');
                    })
          else if (page != null) ...[
            if (!page.canManage)
              Text(
                  'You can see the caps; only a workspace owner can change them.',
                  style: TextStyle(color: p.muted, fontSize: 13)),
            for (final item in page.items)
              _CapTile(
                  key: Key('cap-${item.agentId}'),
                  item: item,
                  controller: c,
                  canManage: page.canManage),
            if (page.total > AgentControlsController.pageSize)
              Wrap(
                  spacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Text(
                        '${c.offset + 1} to ${c.offset + page.items.length} of ${page.total}',
                        style: TextStyle(color: p.muted)),
                    OutlinedButton(
                        onPressed: c.offset == 0
                            ? null
                            : () => c.page(
                                c.offset - AgentControlsController.pageSize),
                        child: const Text('Previous')),
                    OutlinedButton(
                        onPressed: c.offset +
                                    AgentControlsController.pageSize >=
                                page.total
                            ? null
                            : () => c.page(
                                c.offset + AgentControlsController.pageSize),
                        child: const Text('Next')),
                  ]),
          ],
        ],
      ]),
    );
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
  late final cap = TextEditingController(text: _text(widget.item.limitUsd));

  static String _text(double? value) => value == null
      ? ''
      : (value == value.roundToDouble() ? value.toStringAsFixed(0) : '$value');

  @override
  void didUpdateWidget(covariant _CapTile old) {
    super.didUpdateWidget(old);
    if (old.item.limitUsd != widget.item.limitUsd) {
      cap.text = _text(widget.item.limitUsd);
    }
  }

  @override
  void dispose() {
    cap.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final item = widget.item;
    final c = widget.controller;
    final percent = item.percent;
    final color = item.exceeded || (percent ?? 0) >= 100
        ? p.stop
        : (percent ?? 0) >= 80
            ? p.warn
            : p.sky;
    final notice = c.capNotices[item.agentId];
    final limit = item.limitUsd == null ? 'No cap' : formatUsd(item.limitUsd!);
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
              if (item.sharing == 'private') const AnumPill(label: 'Private'),
              if (item.exceeded)
                const AnumPill(label: 'Cap reached', tone: AnumTone.stop)
              else if ((percent ?? 0) >= 80)
                const AnumPill(label: 'Near the cap', tone: AnumTone.warn),
            ]),
        const SizedBox(height: 6),
        Semantics(
          label: 'Share of the cap used by ${item.name}',
          value: percent == null
              ? 'No cap'
              : '${formatUsd(item.costUsd)} of $limit, $percent percent',
          excludeSemantics: true,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text('${formatUsd(item.costUsd)} / $limit',
                style: TextStyle(color: p.text, fontWeight: FontWeight.w800)),
            const SizedBox(height: 4),
            ClipRRect(
              borderRadius: BorderRadius.circular(99),
              child: LinearProgressIndicator(
                  value: item.fraction ?? 0,
                  minHeight: 8,
                  color: color,
                  backgroundColor: p.background),
            ),
            const SizedBox(height: 4),
            Text(
                '${item.calls == 1 ? '1 model call' : '${item.calls} model calls'}${percent == null ? '' : ', $percent% of the cap'}',
                style: TextStyle(color: p.faint, fontSize: 12)),
          ]),
        ),
        if (widget.canManage) ...[
          const SizedBox(height: AnumSpacing.xs),
          Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            Expanded(
              child: TextField(
                key: Key('cap-input-${item.agentId}'),
                controller: cap,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                    labelText: 'Monthly cap (USD) for ${item.name}',
                    hintText: 'No cap'),
              ),
            ),
            const SizedBox(width: AnumSpacing.xs),
            FilledButton(
                key: Key('cap-save-${item.agentId}'),
                onPressed: c.savingAgent != null
                    ? null
                    : () => c.saveCap(item, cap.text),
                child:
                    Text(c.savingAgent == item.agentId ? 'Saving...' : 'Save')),
          ]),
          if (notice != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(notice,
                  style: TextStyle(
                      color:
                          c.failedCaps.contains(item.agentId) ? p.stop : p.ok,
                      fontSize: 12.5)),
            ),
        ],
        Divider(height: 24, color: p.line),
      ]),
    );
  }
}
