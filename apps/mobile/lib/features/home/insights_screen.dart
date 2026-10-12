import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'home_controller.dart';
import 'home_models.dart';
import 'home_widgets.dart';

/// "ANUM Proactive — ANUM noticed a few things" (mockup flow-overview 8):
/// each card has a finding, a question, one primary action and Dismiss /
/// Later. Backed by `GET /api/v1/home/insights` (docs/home.md#insights).
class ProactiveInsightsScreen extends StatefulWidget {
  const ProactiveInsightsScreen({
    required this.controller,
    required this.onOpen,
    required this.onCommand,
    super.key,
  });

  final HomeController controller;
  final ValueChanged<HomeTarget> onOpen;
  final ValueChanged<String?> onCommand;

  @override
  State<ProactiveInsightsScreen> createState() =>
      _ProactiveInsightsScreenState();
}

class _ProactiveInsightsScreenState extends State<ProactiveInsightsScreen> {
  HomeController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_onChange);
    c.loadInsights();
  }

  @override
  void dispose() {
    c.removeListener(_onChange);
    super.dispose();
  }

  void _onChange() {
    if (!mounted) return;
    final notice = c.takeNotice();
    if (notice != null) {
      ScaffoldMessenger.maybeOf(context)
          ?.showSnackBar(SnackBar(content: Text(notice)));
    }
    setState(() {});
  }

  void _act(Insight insight) {
    final action = insight.action;
    Navigator.of(context).maybePop();
    if (action.isCommand) {
      widget.onCommand(action.command);
    } else if (action.target != null) {
      widget.onOpen(action.target!);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    return Scaffold(
      backgroundColor: p.background,
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(t.t('homeProactive')),
          Text(t.t('homeProactiveSub'),
              style: TextStyle(color: p.muted, fontSize: 13)),
        ]),
      ),
      body: AnumPage(onRefresh: c.loadInsights, children: [
        Material(
            type: MaterialType.transparency,
            child: SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(t.t('homeShowHidden')),
              subtitle: c.hiddenInsights == 0
                  ? null
                  : Text(t.f('homeInsightsHidden', {'n': c.hiddenInsights})),
              value: c.showHidden,
              onChanged: c.toggleHidden,
            )),
        ..._body(t),
      ]),
    );
  }

  List<Widget> _body(AnumLocalizations t) {
    switch (c.insightsPhase) {
      case HomePhase.idle:
      case HomePhase.loading:
        if (c.insights.isEmpty) {
          return [
            const Padding(
              padding: EdgeInsets.all(AnumSpacing.xl),
              child: Center(child: CircularProgressIndicator()),
            )
          ];
        }
      case HomePhase.error:
        return [
          AnumSurface(
            child: AnumEmpty(
              icon: Icons.cloud_off_outlined,
              title: t.t('homeInsightsError'),
              message: t.f('homeErrorHint', {'message': c.insightsError ?? ''}),
              action: t.t('retry'),
              onAction: c.loadInsights,
            ),
          )
        ];
      case HomePhase.denied:
        return [
          AnumSurface(
            child: AnumEmpty(
              icon: Icons.lock_outline,
              title: t.t('homeInsightsDenied'),
              message:
                  t.f('homeDeniedHint', {'message': c.insightsError ?? ''}),
            ),
          )
        ];
      case HomePhase.ready:
        break;
    }
    if (c.insights.isEmpty) {
      return [
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.auto_awesome_outlined,
            title: t.t('homeInsightsNone'),
            message: t.t('homeInsightsNoneText'),
            action: t.t('refresh'),
            onAction: c.loadInsights,
          ),
        )
      ];
    }
    return [
      for (final insight in c.insights)
        _InsightCard(
          insight: insight,
          busy: c.busy == 'insight:${insight.key}',
          onAct: () => _act(insight),
          onDismiss: () => c.dismiss(insight),
          onLater: () => c.snooze(insight),
          onRestore: () => c.restore(insight),
        ),
    ];
  }
}

class _InsightCard extends StatelessWidget {
  const _InsightCard({
    required this.insight,
    required this.busy,
    required this.onAct,
    required this.onDismiss,
    required this.onLater,
    required this.onRestore,
  });

  final Insight insight;
  final bool busy;
  final VoidCallback onAct, onDismiss, onLater, onRestore;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final tone = severityTone(insight.severity);
    final local = MaterialLocalizations.of(context);
    final until = insight.snoozedUntil?.toLocal();
    return AnumSurface(
      accent: anumToneColor(context, tone),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          HomeIconSquare(icon: insightIcon(insight), tone: tone),
          const SizedBox(width: AnumSpacing.sm),
          Expanded(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Semantics(
                header: true,
                child: Text(insightText(t, insight, 'title'),
                    style: TextStyle(
                        color: p.text,
                        fontSize: 15.5,
                        fontWeight: FontWeight.w700)),
              ),
              const SizedBox(height: AnumSpacing.xxs),
              Text(insightText(t, insight, 'finding'),
                  style: TextStyle(color: p.muted, fontSize: 13.5)),
              const SizedBox(height: AnumSpacing.xxs),
              Text(insightText(t, insight, 'question'),
                  style: TextStyle(
                      color: p.text,
                      fontSize: 13.5,
                      fontWeight: FontWeight.w600)),
              if (insight.hidden) ...[
                const SizedBox(height: AnumSpacing.xs),
                AnumPill(
                  label: insight.state == 'snoozed' && until != null
                      ? t.f('homeSnoozedUntil', {
                          'time':
                              '${local.formatShortDate(until)} ${local.formatTimeOfDay(TimeOfDay.fromDateTime(until))}'
                        })
                      : t.t('homeDismissed'),
                  tone: AnumTone.neutral,
                ),
              ],
            ]),
          ),
        ]),
        const SizedBox(height: AnumSpacing.md),
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          alignment: WrapAlignment.end,
          children: insight.hidden
              ? [
                  OutlinedButton(
                      onPressed: busy ? null : onRestore,
                      child: Text(t.t('homeRestore'))),
                ]
              : [
                  TextButton(
                      onPressed: busy ? null : onDismiss,
                      child: Text(t.t('homeDismiss'))),
                  OutlinedButton(
                      onPressed: busy ? null : onLater,
                      child: Text(t.t('homeLater'))),
                  HomeGradientButton(
                    label: verbLabel(t, insight.action.verb),
                    onPressed: busy ? null : onAct,
                  ),
                ],
        ),
      ]),
    );
  }
}
