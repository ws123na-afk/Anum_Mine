import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../owner_desk/desk_widgets.dart';
import 'system_health_data.dart';

/// System health on a phone (docs/system-health.md#on-the-phone): every check
/// with its state, one line from its numbers and what to do, problems first.
/// The numbers behind each check and the ids the owner notices need are on
/// the web's platform console.
class SystemHealthScreen extends StatefulWidget {
  const SystemHealthScreen({required this.controller, super.key});
  final SystemHealthController controller;

  @override
  State<SystemHealthScreen> createState() => _SystemHealthScreenState();
}

class _SystemHealthScreenState extends State<SystemHealthScreen> {
  SystemHealthController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final l = AnumLocalizations.of(context);
          return Scaffold(
            appBar: AppBar(title: Text(l.t('health.title')), actions: [
              IconButton(
                  key: const Key('sh-refresh'),
                  tooltip: l.t('health.recheck'),
                  onPressed: c.phase == HealthPhase.loading ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                  eyebrow: l.t('od.eyebrow'),
                  title: l.t('health.title'),
                  subtitle: l.t('health.lead'),
                  large: true),
              ..._body(context),
            ]),
          );
        },
      );

  List<Widget> _body(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    switch (c.phase) {
      case HealthPhase.loading:
        return [DeskLoading(label: l.t('health.loading'))];
      case HealthPhase.denied:
        return [
          AnumSurface(
              child: AnumEmpty(
                  key: const Key('sh-denied'),
                  icon: Icons.lock_outline,
                  title: l.t('sh.deniedTitle'),
                  message: l.t('sh.denied')))
        ];
      case HealthPhase.error:
        return [
          AnumSurface(
              accent: p.stop,
              child: AnumEmpty(
                  key: const Key('sh-error'),
                  icon: Icons.error_outline,
                  title: l.t('od.loadFailedTitle'),
                  message: l.f('sh.failed', {'message': c.error ?? '-'}),
                  action: l.t('od.retry'),
                  onAction: c.load))
        ];
      case HealthPhase.ready:
        break;
    }
    final h = c.health!;
    final attention = h.attention;
    final tone = attention == 0
        ? AnumTone.ok
        : h.overall == 'red'
            ? AnumTone.stop
            : AnumTone.warn;
    return [
      AnumSurface(
        key: const Key('sh-summary'),
        accent: anumToneColor(context, tone),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                AnumPill(
                    label: attention == 0
                        ? l.t('health.allGood')
                        : l.f('sh.attention', {'count': attention}),
                    tone: tone),
                Text(
                    l.f('sh.checked', {'when': deskWhen(context, h.checkedAt)}),
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
              ]),
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('sh.webMore'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        ]),
      ),
      for (final item in h.sorted) _item(context, item),
    ];
  }

  Widget _item(BuildContext context, HealthItem item) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final (icon, tone) = switch (item.state) {
      'green' => (Icons.check_circle_outline, AnumTone.ok),
      'amber' => (Icons.error_outline, AnumTone.warn),
      'red' => (Icons.cancel_outlined, AnumTone.stop),
      _ => (Icons.radio_button_unchecked, AnumTone.neutral),
    };
    final showFix = !item.green &&
        !(item.key == 'restarts' && item.detail['configured'] == false);
    return AnumSurface(
      key: Key('sh-item-${item.key}'),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(icon, color: anumToneColor(context, tone)),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xxs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Semantics(
                    header: true,
                    child: Text(l.t('health.item.${item.key}'),
                        style: TextStyle(
                            color: p.text,
                            fontSize: AnumType.base,
                            fontWeight: FontWeight.w800)),
                  ),
                  AnumPill(
                      label: l.t('health.state.${item.state}'), tone: tone),
                ]),
            const SizedBox(height: AnumSpacing.xxs),
            Text(healthLine(l, item, (d) => deskWhen(context, d)),
                style: TextStyle(color: p.text, fontSize: AnumType.sm)),
            if (showFix) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text.rich(
                  TextSpan(children: [
                    TextSpan(
                        text: '${l.t('health.fixLabel')} ',
                        style: const TextStyle(fontWeight: FontWeight.w800)),
                    TextSpan(text: l.t('health.fix.${item.key}')),
                  ]),
                  style: TextStyle(color: p.text, fontSize: AnumType.sm)),
            ],
          ]),
        ),
      ]),
    );
  }
}
