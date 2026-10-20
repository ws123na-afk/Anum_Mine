import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'whatsapp_controller.dart';
import 'whatsapp_models.dart';
import 'whatsapp_ops_controller.dart';

/// Text direction for free text that may be Arabic inside an English page
/// (and the other way round): the first strong letter decides.
TextDirection? waTextDirection(String text) {
  for (final unit in text.runes) {
    if ((unit >= 0x0590 && unit <= 0x08FF) ||
        (unit >= 0xFB1D && unit <= 0xFEFC)) {
      return TextDirection.rtl;
    }
    if ((unit >= 0x41 && unit <= 0x5A) || (unit >= 0x61 && unit <= 0x7A)) {
      return TextDirection.ltr;
    }
  }
  return null;
}

/// A template's review status in words ("approved", "pending", ...).
String waTemplateStatus(AnumLocalizations l, String status) {
  const known = {'APPROVED', 'PENDING', 'REJECTED', 'PAUSED', 'DISABLED'};
  return known.contains(status)
      ? l.t('waOps.tstatus.$status')
      : status.toLowerCase();
}

/// A template's category in words ("marketing", "utility", ...).
String waTemplateCategory(AnumLocalizations l, String category) {
  const known = {'MARKETING', 'UTILITY', 'AUTHENTICATION'};
  return known.contains(category)
      ? l.t('waOps.tcat.$category')
      : category.toLowerCase();
}

AnumTone waTemplateTone(String status) => switch (status) {
      'APPROVED' => AnumTone.ok,
      'REJECTED' || 'PAUSED' || 'DISABLED' => AnumTone.stop,
      _ => AnumTone.warn,
    };

/// Templates (docs/whatsapp.md#templates-and-setup): every message template
/// of the connected WhatsApp account with status, category, language, body
/// and parameter count; search; refresh from Meta; and, for owners, the
/// switch that lets an approved template go out without waiting for an
/// approval (the server still applies its approval and autonomy rules).
class WhatsAppTemplatesScreen extends StatefulWidget {
  const WhatsAppTemplatesScreen(
      {required this.controller, this.onOpenSetup, super.key});
  final WaTemplatesController controller;

  /// Opens the Setup screen from the not-connected state; null hides it.
  final VoidCallback? onOpenSetup;

  @override
  State<WhatsAppTemplatesScreen> createState() =>
      _WhatsAppTemplatesScreenState();
}

class _WhatsAppTemplatesScreenState extends State<WhatsAppTemplatesScreen> {
  final _search = TextEditingController();
  WaTemplatesController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  void dispose() {
    _search.dispose();
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('waOps.templates.title')), actions: [
        ListenableBuilder(
            listenable: c,
            builder: (context, _) => IconButton(
                key: const Key('wa-templates-refresh'),
                onPressed: c.busy == null ? () => c.load(refresh: true) : null,
                tooltip: l.t('waOps.templates.refresh'),
                icon: const Icon(Icons.refresh))),
      ]),
      body: ListenableBuilder(
          listenable: c,
          builder: (context, _) => AnumPage(
              onRefresh: () => c.load(refresh: true), children: _body(l))),
    );
  }

  List<Widget> _body(AnumLocalizations l) {
    switch (c.phase) {
      case WaPhase.initial:
      case WaPhase.loading:
        return [
          Semantics(
              liveRegion: true,
              label: l.t('waOps.templates.loading'),
              child: const Center(
                  child: Padding(
                      padding: EdgeInsets.all(AnumSpacing.lg),
                      child: CircularProgressIndicator())))
        ];
      case WaPhase.denied:
        return [
          AnumEmpty(
              icon: Icons.lock_outline,
              title: l.t('waOps.templates.deniedTitle'),
              message: c.error ?? l.t('waDeniedText'))
        ];
      case WaPhase.notConnected:
        return [
          AnumEmpty(
              icon: Icons.power_outlined,
              title: l.t('waNotConnected'),
              message: c.canManage
                  ? l.t('waOps.notConnectedOwner')
                  : l.t('waNotConnectedMember'),
              action: c.canManage && widget.onOpenSetup != null
                  ? l.t('waOps.setup.open')
                  : null,
              onAction: widget.onOpenSetup)
        ];
      case WaPhase.error:
        return [
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('waOps.templates.failed'),
              message: c.error ?? '',
              action: l.t('retry'),
              onAction: () => c.load(refresh: true))
        ];
      case WaPhase.ready:
        break;
    }
    final text = Theme.of(context).textTheme;
    final p = context.palette;
    final shown = c.visible;
    return [
      Text(l.t('waOps.templates.intro'),
          style: text.bodyMedium?.copyWith(color: p.muted)),
      if (c.notice != null || c.noticeKey != null)
        Semantics(
            liveRegion: true,
            child: Text(
                c.noticeKey != null
                    ? l.f(c.noticeKey!, c.noticeValues)
                    : c.notice!,
                style: text.bodyMedium
                    ?.copyWith(color: c.noticeFailed ? p.stop : p.ok))),
      if (c.templates.isNotEmpty)
        TextField(
            key: const Key('wa-templates-search'),
            controller: _search,
            textInputAction: TextInputAction.search,
            onChanged: c.search,
            decoration: InputDecoration(
                labelText: l.t('waOps.templates.search'),
                prefixIcon: const Icon(Icons.search),
                suffixIcon: c.query.isEmpty
                    ? null
                    : IconButton(
                        tooltip: l.t('waClearSearch'),
                        onPressed: () {
                          _search.clear();
                          c.search('');
                        },
                        icon: const Icon(Icons.clear)))),
      if (c.templates.isEmpty)
        AnumEmpty(
            icon: Icons.description_outlined,
            title: l.t('waOps.templates.emptyTitle'),
            message: l.t('waOps.templates.emptyText'),
            action: l.t('waOps.templates.refresh'),
            onAction: () => c.load(refresh: true))
      else if (shown.isEmpty)
        AnumEmpty(
            icon: Icons.search_off,
            title: l.t('waOps.templates.noMatch'),
            message: l.t('waOps.templates.noMatchText'),
            action: l.t('waShowAll'),
            onAction: () {
              _search.clear();
              c.search('');
            })
      else
        for (final template in shown)
          _TemplateCard(template: template, controller: c),
      if (c.hasMoreToShow)
        Center(
            child: OutlinedButton(
                key: const Key('wa-templates-more'),
                onPressed: c.showMore,
                child: Text(l.f('waOps.templates.more',
                    {'count': c.matches.length - c.shown})))),
      if (c.fetchedAt != null)
        Text(l.f('waOps.templates.fetched', {'time': exactTime(c.fetchedAt!)}),
            style: text.bodySmall),
      Text(l.t('waOps.templates.autoExplain'), style: text.bodySmall),
    ];
  }
}

class _TemplateCard extends StatelessWidget {
  const _TemplateCard({required this.template, required this.controller});
  final WaTemplate template;
  final WaTemplatesController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final status = waTemplateStatus(l, template.status);
    final busy = controller.busy == template.key;
    return Semantics(
      container: true,
      label: [
        template.name,
        status,
        template.language,
        if (template.autoSend) l.t('waOps.templates.autoShort'),
      ].join(', '),
      child: AnumSurface(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(template.name,
              textDirection: TextDirection.ltr,
              style: text.titleMedium,
              softWrap: true),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xxs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                AnumPill(label: status, tone: waTemplateTone(template.status)),
                AnumPill(label: waTemplateCategory(l, template.category)),
                AnumPill(label: template.language),
                if (template.parameterCount > 0)
                  AnumPill(
                      label: l.f('waOps.templates.params',
                          {'count': template.parameterCount})),
                if (template.autoSend)
                  AnumPill(
                      icon: Icons.bolt,
                      label: l.t('waOps.templates.autoShort'),
                      tone: AnumTone.accent),
              ]),
          if ((template.header ?? '').isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(template.header!,
                textDirection: waTextDirection(template.header!),
                style: text.titleSmall),
          ],
          const SizedBox(height: AnumSpacing.xs),
          Text(template.body,
              textDirection: waTextDirection(template.body),
              style: text.bodyMedium),
          if ((template.footer ?? '').isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xxs),
            Text(template.footer!,
                textDirection: waTextDirection(template.footer!),
                style: text.bodySmall),
          ],
          if (template.buttons.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xxs),
            Text(template.buttons.join(' / '),
                textDirection: waTextDirection(template.buttons.first),
                style: text.bodySmall?.copyWith(color: p.faint)),
          ],
          if (controller.canManage && template.approved)
            SwitchListTile(
                key: Key('wa-auto-${template.key}'),
                contentPadding: EdgeInsets.zero,
                title: Text(l.t('waOps.templates.autoLabel')),
                subtitle: busy ? Text(l.t('waOps.saving')) : null,
                value: template.autoSend,
                onChanged: controller.busy == null
                    ? (on) => controller.setAutoSend(template, on)
                    : null),
        ]),
      ),
    );
  }
}
