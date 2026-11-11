import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../owner_desk/desk_widgets.dart';
import 'selling_data.dart';

/// Ready to sell on a phone (docs/selling-readiness.md#mobile): the checklist
/// with each item's state, what is missing and what to do next, and the
/// mail domain's DNS check. Payment keys, company details, legal pages and
/// plans are changed on the web (long forms with secrets and a lawyer's
/// review); each item says where.
class SellingScreen extends StatefulWidget {
  const SellingScreen({required this.controller, super.key});
  final SellingController controller;

  @override
  State<SellingScreen> createState() => _SellingScreenState();
}

class _SellingScreenState extends State<SellingScreen> {
  SellingController get c => widget.controller;
  final _domain = TextEditingController();
  bool _domainSet = false;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  void dispose() {
    _domain.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final l = AnumLocalizations.of(context);
          final r = c.readiness;
          if (!_domainSet && r?.mailDomain != null) {
            _domainSet = true;
            _domain.text = r!.mailDomain!;
          }
          return Scaffold(
            appBar: AppBar(title: Text(l.t('selling.ready.title')), actions: [
              IconButton(
                  key: const Key('rts-refresh'),
                  tooltip: l.t('selling.ready.recheck'),
                  onPressed: c.phase == SellingPhase.loading ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                  eyebrow: l.t('od.eyebrow'),
                  title: l.t('selling.ready.title'),
                  subtitle: l.t('selling.ready.lead'),
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
      case SellingPhase.loading:
        return [DeskLoading(label: l.t('selling.ready.loading'))];
      case SellingPhase.denied:
        return [
          AnumSurface(
              child: AnumEmpty(
                  key: const Key('rts-denied'),
                  icon: Icons.lock_outline,
                  title: l.t('od.rts.deniedTitle'),
                  message: l.t('selling.ready.denied')))
        ];
      case SellingPhase.error:
        return [
          AnumSurface(
              accent: p.stop,
              child: AnumEmpty(
                  key: const Key('rts-error'),
                  icon: Icons.error_outline,
                  title: l.t('od.loadFailedTitle'),
                  message:
                      l.f('selling.ready.failed', {'message': c.error ?? '-'}),
                  action: l.t('od.retry'),
                  onAction: c.load))
        ];
      case SellingPhase.ready:
        break;
    }
    final r = c.readiness!;
    return [
      AnumSurface(
        key: const Key('rts-summary'),
        accent: r.ready ? p.ok : p.warn,
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                AnumPill(
                    label: l.f('selling.ready.count',
                        {'done': r.done, 'total': r.total}),
                    tone: r.ready ? AnumTone.ok : AnumTone.warn),
                Text(
                    l.f('selling.ready.checked',
                        {'when': deskWhen(context, r.checkedAt)}),
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
              ]),
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('od.rts.webOnly'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          const SizedBox(height: AnumSpacing.sm),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
                onPressed: c.load,
                icon: const Icon(Icons.refresh),
                label: Text(l.t('selling.ready.recheck'))),
          ),
        ]),
      ),
      for (final item in r.sorted) _item(context, item),
      _dnsCard(context),
    ];
  }

  Widget _item(BuildContext context, ReadinessItem item) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final (icon, tone) = switch (item.state) {
      'done' => (Icons.check_circle_outline, AnumTone.ok),
      'warning' => (Icons.error_outline, AnumTone.warn),
      'unknown' => (Icons.radio_button_unchecked, AnumTone.neutral),
      _ => (Icons.cancel_outlined, AnumTone.stop),
    };
    final lines = readinessLines(l, item, (d) => deskWhen(context, d));
    return AnumSurface(
      key: Key('rts-item-${item.key}'),
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
                    child: Text(l.t('selling.ready.item.${item.key}'),
                        style: TextStyle(
                            color: p.text,
                            fontSize: AnumType.base,
                            fontWeight: FontWeight.w800)),
                  ),
                  AnumPill(
                      label: l.t('selling.ready.state.${item.state}'),
                      tone: tone),
                ]),
            const SizedBox(height: AnumSpacing.xxs),
            Text(l.t('selling.ready.why.${item.key}'),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            for (final line in lines)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xxs),
                child: Text(line,
                    style: TextStyle(color: p.text, fontSize: AnumType.sm)),
              ),
            if (!item.done) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text.rich(
                  TextSpan(children: [
                    TextSpan(
                        text: '${l.t('selling.ready.fixLabel')} ',
                        style: const TextStyle(fontWeight: FontWeight.w800)),
                    TextSpan(text: l.t('selling.ready.fix.${item.key}')),
                  ]),
                  style: TextStyle(color: p.text, fontSize: AnumType.sm)),
              if (item.webTab != null)
                Padding(
                  padding: const EdgeInsets.only(top: AnumSpacing.xxs),
                  child: Row(children: [
                    Icon(Icons.desktop_windows_outlined,
                        size: 16, color: p.muted),
                    const SizedBox(width: AnumSpacing.xs),
                    Expanded(
                        child: Text(l.t('od.rts.onWeb.${item.webTab}'),
                            style: TextStyle(
                                color: p.muted, fontSize: AnumType.xs))),
                  ]),
                ),
            ],
          ]),
        ),
      ]),
    );
  }

  Widget _dnsCard(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final result = c.dns;
    final problem = c.dnsProblemText(l);
    AnumTone spfTone(String s) => s == 'ok' ? AnumTone.ok : AnumTone.stop;
    AnumTone dmarcTone(String s) => s == 'ok'
        ? AnumTone.ok
        : s == 'monitor'
            ? AnumTone.warn
            : AnumTone.stop;
    Widget record(String name, DnsRecordCheck check, String label,
            AnumTone tone, String? help) =>
        Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const SizedBox(height: AnumSpacing.sm),
          Wrap(
              spacing: AnumSpacing.xs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(name,
                    style:
                        TextStyle(color: p.text, fontWeight: FontWeight.w800)),
                AnumPill(label: label, tone: tone),
              ]),
          const SizedBox(height: AnumSpacing.xxs),
          if (check.record != null)
            SelectableText(check.record!,
                textDirection: TextDirection.ltr,
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.xs,
                    fontFamily: 'monospace',
                    fontFamilyFallback: const ['Courier']))
          else
            Text(
                check.error != null
                    ? l.f('selling.dns.lookupFailed', {'error': check.error!})
                    : l.t('selling.dns.noRecord'),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          if (help != null)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xxs),
              child: Text(help,
                  style: TextStyle(color: p.text, fontSize: AnumType.sm)),
            ),
        ]);
    return AnumSurface(
      key: const Key('rts-dns'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
            title: l.t('selling.dns.title'), subtitle: l.t('selling.dns.lead')),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
          key: const Key('rts-domain'),
          controller: _domain,
          textDirection: TextDirection.ltr,
          keyboardType: TextInputType.url,
          autocorrect: false,
          textInputAction: TextInputAction.search,
          onSubmitted: c.checking ? null : c.check,
          decoration: InputDecoration(
              labelText: l.t('selling.dns.domain'), hintText: 'example.com'),
        ),
        const SizedBox(height: AnumSpacing.sm),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
              key: const Key('rts-dns-check'),
              onPressed: c.checking ? null : () => c.check(_domain.text),
              icon: const Icon(Icons.dns_outlined),
              label: Text(c.checking
                  ? l.t('selling.dns.checking')
                  : l.t('selling.dns.check'))),
        ),
        if (problem != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          DeskMessage(
              key: const Key('rts-dns-problem'), text: problem, failed: true),
        ],
        if (result != null)
          Semantics(
            liveRegion: true,
            container: true,
            child: Column(
                key: const Key('rts-dns-result'),
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  record(
                      'SPF',
                      result.spf,
                      l.t('selling.dns.spf.${result.spf.status}'),
                      spfTone(result.spf.status),
                      l.t('selling.dns.spfHelp.${result.spf.status}')),
                  record(
                      'DMARC',
                      result.dmarc,
                      l.t('selling.dns.dmarc.${result.dmarc.status}'),
                      dmarcTone(result.dmarc.status),
                      null),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(
                      l.f('selling.dns.checkedAt', {
                        'domain': result.domain,
                        'when': deskWhen(context, result.checkedAt)
                      }),
                      style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                ]),
          ),
      ]),
    );
  }
}
