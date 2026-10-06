import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'admin_controller.dart';
import 'admin_models.dart';

/// Owner screen: the organization's and this workspace's monthly model
/// budgets with this month's usage (docs/model-gateway.md, Monthly budgets).
class BudgetsScreen extends StatefulWidget {
  const BudgetsScreen({required this.controller, super.key});
  final BudgetsController controller;
  @override
  State<BudgetsScreen> createState() => _BudgetsScreenState();
}

class _BudgetsScreenState extends State<BudgetsScreen> {
  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final p = context.palette;
          final o = c.overview;
          return Scaffold(
            appBar: AppBar(title: const Text('Model budgets'), actions: [
              IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.savingScope != null ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                eyebrow: 'Model spend',
                title: 'Monthly model budgets',
                subtitle: o == null
                    ? 'Estimated cost and token limits per UTC calendar month.'
                    : 'Usage since ${formatResetDate(o.periodStart).replaceAll(' (UTC)', '')}. Resets on ${formatResetDate(o.resetsOn)}.',
                large: true,
              ),
              ...switch (c.phase) {
                AdminPhase.initial || AdminPhase.loading => [
                    const Padding(
                        padding: EdgeInsets.all(AnumSpacing.lg),
                        child: Center(child: CircularProgressIndicator()))
                  ],
                AdminPhase.forbidden => [
                    AnumSurface(
                        accent: p.warn,
                        child: AnumEmpty(
                          icon: Icons.lock_outline,
                          title: 'Owner access required',
                          message:
                              'Model budgets and usage are visible to organization owners only. '
                              'If tasks stop with a budget message, ask an owner to raise the limit.\n'
                              'The API answered: ${c.loadMessage}',
                        )),
                  ],
                AdminPhase.offline || AdminPhase.error => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                          icon: Icons.error_outline,
                          title: 'Budgets could not be loaded',
                          message: c.loadMessage ?? 'Try again.',
                          action: 'Retry',
                          onAction: c.load,
                        )),
                  ],
                AdminPhase.ready => [
                    if (o!.tenant.exceeded || o.workspace.exceeded)
                      AnumSurface(
                        accent: p.stop,
                        child: Text(
                            'A monthly budget is used up: task runs are refused and voice answers say so until ${formatResetDate(o.resetsOn)} or until an owner raises the limit.',
                            style: TextStyle(color: p.text)),
                      ),
                    _BudgetCard(
                        key: const Key('budget-tenant'),
                        scope: 'tenant',
                        title: 'Organization',
                        subtitle: 'All workspaces in this organization',
                        view: o.tenant,
                        controller: c),
                    _BudgetCard(
                        key: const Key('budget-workspace'),
                        scope: 'workspace',
                        title: 'This workspace',
                        subtitle: 'Task planning and voice answers here',
                        view: o.workspace,
                        controller: c),
                  ],
              },
            ]),
          );
        },
      );
}

class _BudgetCard extends StatefulWidget {
  const _BudgetCard({
    required this.scope,
    required this.title,
    required this.subtitle,
    required this.view,
    required this.controller,
    super.key,
  });
  final String scope, title, subtitle;
  final BudgetScopeView view;
  final BudgetsController controller;
  @override
  State<_BudgetCard> createState() => _BudgetCardState();
}

class _BudgetCardState extends State<_BudgetCard> {
  late final cost = TextEditingController(
      text: limitText(widget.view.budget?.limits.costUsd));
  late final tokens =
      TextEditingController(text: limitText(widget.view.budget?.limits.tokens));

  @override
  void didUpdateWidget(covariant _BudgetCard old) {
    super.didUpdateWidget(old);
    if (old.view.budget?.updatedAt != widget.view.budget?.updatedAt) {
      cost.text = limitText(widget.view.budget?.limits.costUsd);
      tokens.text = limitText(widget.view.budget?.limits.tokens);
    }
  }

  @override
  void dispose() {
    cost.dispose();
    tokens.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final v = widget.view;
    final c = widget.controller;
    final saving = c.savingScope == widget.scope;
    final notice = c.notices[widget.scope];
    final failed = c.failedScopes.contains(widget.scope);
    return AnumSurface(
      accent: v.exceeded ? p.stop : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          eyebrow:
              widget.scope == 'tenant' ? 'Tenant scope' : 'Workspace scope',
          title: widget.title,
          subtitle: widget.subtitle,
          trailing: AnumPill(
              label: v.exceeded
                  ? 'Used up'
                  : v.budget == null
                      ? 'No budget'
                      : 'Limited',
              tone: v.exceeded
                  ? AnumTone.stop
                  : v.budget == null
                      ? AnumTone.neutral
                      : AnumTone.ok),
        ),
        const SizedBox(height: 14),
        for (final meter in v.meters) _Meter(meter: meter, scope: widget.title),
        Wrap(spacing: 18, runSpacing: 8, children: [
          _Stat('Model calls', formatTokens(v.calls)),
          _Stat('Input tokens', formatTokens(v.inputTokens)),
          _Stat('Output tokens', formatTokens(v.outputTokens)),
          _Stat('Unpriced calls', formatTokens(v.unpricedCalls)),
        ]),
        if (v.unpricedCalls > 0) ...[
          const SizedBox(height: 8),
          Text('Unpriced calls add tokens but no cost: set a token limit too.',
              style: TextStyle(color: p.muted, fontSize: 12.5)),
        ],
        if (v.budget != null) ...[
          const SizedBox(height: 8),
          Text(
              'Last changed ${exactTime(v.budget!.updatedAt)} by ${v.budget!.updatedBy}.',
              style: TextStyle(color: p.faint, fontSize: 12)),
        ],
        Divider(height: 28, color: p.line),
        TextField(
            key: Key('${widget.scope}-cost'),
            controller: cost,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
                labelText: 'Monthly cost limit (USD)', hintText: 'No limit')),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
            key: Key('${widget.scope}-tokens'),
            controller: tokens,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
                labelText: 'Monthly token limit', hintText: 'No limit')),
        const SizedBox(height: 6),
        Text('Leave a field empty for no limit. 0 blocks every model call.',
            style: TextStyle(color: p.faint, fontSize: 12)),
        if (notice != null) ...[
          const SizedBox(height: 8),
          Text(notice,
              key: Key('${widget.scope}-notice'),
              style: TextStyle(color: failed ? p.stop : p.ok)),
        ],
        const SizedBox(height: 12),
        Wrap(spacing: 8, runSpacing: 8, children: [
          FilledButton.icon(
              onPressed: c.savingScope != null
                  ? null
                  : () => c.save(widget.scope, cost.text, tokens.text),
              icon: const Icon(Icons.save_outlined),
              label: Text(saving ? 'Saving...' : 'Save limits')),
          if (v.budget != null)
            OutlinedButton(
                onPressed: c.savingScope != null
                    ? null
                    : () => c.save(widget.scope, '', ''),
                child: const Text('Remove limits')),
        ]),
      ]),
    );
  }
}

class _Meter extends StatelessWidget {
  const _Meter({required this.meter, required this.scope});
  final BudgetMeter meter;
  final String scope;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final color = switch (meter.tone) {
      MeterTone.stop => p.stop,
      MeterTone.warn => p.warn,
      _ => p.sky,
    };
    return Semantics(
      label: '$scope ${meter.label.toLowerCase()}',
      value: '${meter.usedText} of ${meter.limitText}, ${meter.percentText}',
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Expanded(
                child: Text(meter.label,
                    style: TextStyle(
                        color: p.muted,
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600))),
            Text(meter.usedText,
                style: TextStyle(
                    color: p.text, fontSize: 17, fontWeight: FontWeight.w800)),
            Text(' / ${meter.limitText}',
                style: TextStyle(color: p.muted, fontSize: 12.5)),
          ]),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(99),
            child: LinearProgressIndicator(
              value: meter.fraction,
              minHeight: 10,
              color: color,
              backgroundColor: p.background,
            ),
          ),
          const SizedBox(height: 4),
          Text(meter.percentText,
              style: TextStyle(color: p.faint, fontSize: 12)),
        ]),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value);
  final String label, value;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: TextStyle(color: p.faint, fontSize: 11.5)),
      Text(value, style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
    ]);
  }
}
