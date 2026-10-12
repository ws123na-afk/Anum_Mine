import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'home_models.dart';

/// Feature-local building blocks of Home (docs/home.md). They stay in this
/// folder while the shell owns the shared widget files; candidates to move
/// into `lib/src/widgets` later.

/// One ANUM feature Home can open: its label key and icon.
class HomeFeature {
  const HomeFeature(this.key, this.labelKey, this.icon, this.tone);
  final String key, labelKey;
  final IconData icon;
  final AnumTone tone;
}

/// The shortcut grid, in the mockup's order. `command` opens ANUM Command.
const homeShortcuts = <HomeFeature>[
  HomeFeature('command', 'homeTileCommand', Icons.graphic_eq, AnumTone.accent),
  HomeFeature('chat', 'homeTileChat', Icons.chat_bubble_outline, AnumTone.info),
  HomeFeature('agents', 'homeTileAgents', Icons.groups_outlined, AnumTone.info),
  HomeFeature(
      'voice', 'homeTileVoice', Icons.videocam_outlined, AnumTone.accent),
  HomeFeature('automations', 'homeTileAutomations', Icons.auto_mode_outlined,
      AnumTone.ok),
  HomeFeature('tasks', 'homeTileTasks', Icons.task_alt, AnumTone.stop),
  HomeFeature(
      'calendar', 'homeTileCalendar', Icons.event_outlined, AnumTone.info),
  HomeFeature(
      'reports', 'homeTileReports', Icons.bar_chart_outlined, AnumTone.accent),
  HomeFeature('documents', 'homeTileDocuments', Icons.description_outlined,
      AnumTone.warn),
  HomeFeature('integrations', 'homeTileIntegrations', Icons.hub_outlined,
      AnumTone.info),
  HomeFeature('knowledge', 'homeTileKnowledge', Icons.menu_book_outlined,
      AnumTone.accent),
  HomeFeature(
      'commerce', 'homeTileCommerce', Icons.storefront_outlined, AnumTone.ok),
  HomeFeature('more', 'homeTileMore', Icons.more_horiz, AnumTone.neutral),
];

/// Features a pin may open, beyond the shortcuts.
const homeExtraFeatures = <HomeFeature>[
  HomeFeature('approvals', 'homeFeatureApprovals', Icons.front_hand_outlined,
      AnumTone.warn),
  HomeFeature('email', 'homeFeatureEmail', Icons.mail_outline, AnumTone.info),
  HomeFeature(
      'whatsapp', 'homeFeatureWhatsapp', Icons.forum_outlined, AnumTone.ok),
  HomeFeature('autonomy', 'homeFeatureAutonomy', Icons.tune, AnumTone.accent),
  HomeFeature('settings', 'homeFeatureSettings', Icons.settings_outlined,
      AnumTone.neutral),
  HomeFeature(
      'team', 'homeFeatureTeam', Icons.groups_2_outlined, AnumTone.info),
  HomeFeature(
      'favorites', 'homeFeatureFavorites', Icons.star_outline, AnumTone.warn),
  HomeFeature(
      'tyres', 'homeFeatureTyres', Icons.tire_repair_outlined, AnumTone.ok),
  HomeFeature('marketing', 'homeFeatureMarketing', Icons.campaign_outlined,
      AnumTone.accent),
  HomeFeature('insights', 'homeProactive', Icons.auto_awesome_outlined,
      AnumTone.accent),
];

HomeFeature? homeFeature(String key) {
  for (final f in [...homeShortcuts, ...homeExtraFeatures]) {
    if (f.key == key) return f;
  }
  return null;
}

/// Icons a pin can carry (the API stores the name).
const homePinIcons = <String, IconData>{
  'apps': Icons.apps,
  'tyre': Icons.tire_repair,
  'truck': Icons.local_shipping_outlined,
  'chart': Icons.insights_outlined,
  'store': Icons.storefront_outlined,
  'mic': Icons.mic_none,
  'docs': Icons.description_outlined,
};

IconData pinIcon(HomePin pin) {
  final named = homePinIcons[pin.icon];
  if (named != null) return named;
  return switch (pin.kind) {
    PinKind.workspace => Icons.workspaces_outline,
    PinKind.link => Icons.open_in_new,
    PinKind.feature => homeFeature(pin.target)?.icon ?? Icons.apps,
  };
}

// -- localized text ------------------------------------------------------------

String commandLabel(AnumLocalizations t, QuickCommand c) {
  final key = c.key;
  if (key == null) return c.label;
  final value = t.t('homeCmd_$key');
  return value == 'homeCmd_$key' ? c.label : value;
}

String commandPrompt(AnumLocalizations t, QuickCommand c) {
  final key = c.key;
  if (key == null) return c.prompt;
  final value = t.t('homeCmdPrompt_$key');
  return value == 'homeCmdPrompt_$key' ? c.prompt : value;
}

String _orFallback(AnumLocalizations t, String key, Map<String, Object> params,
    String fallback) {
  if (t.t(key) == key) return fallback;
  return t.f(key, params);
}

Map<String, Object> _params(Map<String, Object?> raw) => {
      for (final e in raw.entries)
        if (e.value != null) e.key: e.value!,
    };

String statusLabel(AnumLocalizations t, String status) =>
    _orFallback(t, 'homeStatus_$status', const {}, status);

String focusTitle(AnumLocalizations t, FocusItem item) => switch (item.kind) {
      'task_in_progress' => item.title,
      _ => _orFallback(
          t, 'homeFocus_${item.kind}', _params(item.params), item.title),
    };

String? focusDetail(AnumLocalizations t, FocusItem item) {
  switch (item.kind) {
    case 'approvals_pending':
      return t.t('homeFocusNeedsDecision');
    case 'email_urgent':
      return t.t('homeFocusNeedsReply');
    case 'task_in_progress':
      final raw = item.params['status'];
      final status = statusLabel(t, raw is String ? raw : '');
      final progress = item.progress;
      return progress == null
          ? status
          : '$status · ${t.f('homePercent', {
                  'percent': (progress * 100).round()
                })}';
    default:
      return item.detail;
  }
}

IconData focusIcon(FocusItem item) => switch (item.kind) {
      'approvals_pending' => Icons.front_hand_outlined,
      'email_urgent' => Icons.mark_email_unread_outlined,
      'automations_failed' => Icons.error_outline,
      'meeting_today' => Icons.groups_2_outlined,
      'task_in_progress' => Icons.bolt_outlined,
      _ => homeFeature(item.target.feature)?.icon ?? Icons.flag_outlined,
    };

AnumTone severityTone(HomeSeverity s) => switch (s) {
      HomeSeverity.high => AnumTone.stop,
      HomeSeverity.medium => AnumTone.warn,
      HomeSeverity.low => AnumTone.info,
    };

String insightText(AnumLocalizations t, Insight i, String part) {
  final fallback = switch (part) {
    'title' => i.title,
    'finding' => i.finding,
    _ => i.question,
  };
  return _orFallback(
      t, 'homeInsight_${i.kind}_$part', _params(i.params), fallback);
}

IconData insightIcon(Insight i) => switch (i.kind) {
      'autonomy_proposal' => Icons.tune,
      'approval_waiting' => Icons.front_hand_outlined,
      'task_failed' => Icons.report_gmailerrorred_outlined,
      'task_stalled' => Icons.hourglass_bottom,
      'automation_failing' => Icons.auto_mode_outlined,
      'integration_degraded' => Icons.hub_outlined,
      'mailbox_reauth' => Icons.mail_lock_outlined,
      'email_urgent' => Icons.mark_email_unread_outlined,
      _ => Icons.lightbulb_outline,
    };

String verbLabel(AnumLocalizations t, String verb) =>
    _orFallback(t, 'homeVerb_$verb', const {}, verb);

String agoLabel(AnumLocalizations t, DateTime at, DateTime now) {
  final diff = now.difference(at.toLocal());
  if (diff.inMinutes < 1) return t.t('homeAgoNow');
  if (diff.inHours < 1) return t.f('homeAgoMin', {'n': diff.inMinutes});
  if (diff.inDays < 1) return t.f('homeAgoHour', {'n': diff.inHours});
  return t.f('homeAgoDay', {'n': diff.inDays});
}

// -- widgets ---------------------------------------------------------------------

/// A gradient icon square in one of the palette tones.
class HomeIconSquare extends StatelessWidget {
  const HomeIconSquare(
      {required this.icon,
      this.tone = AnumTone.info,
      this.size = 44,
      super.key});
  final IconData icon;
  final AnumTone tone;
  final double size;

  @override
  Widget build(BuildContext context) {
    final color = anumToneColor(context, tone);
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(size * 0.3),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            color.withValues(alpha: 0.38),
            color.withValues(alpha: 0.14)
          ],
        ),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Icon(icon, size: size * 0.5, color: color),
    );
  }
}

/// One shortcut tile: icon square, label, optional caption.
class HomeTile extends StatelessWidget {
  const HomeTile({
    required this.icon,
    required this.label,
    required this.onTap,
    this.caption,
    this.tone = AnumTone.info,
    this.highlighted = false,
    super.key,
  });

  final IconData icon;
  final String label;
  final String? caption;
  final AnumTone tone;
  final bool highlighted;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final radius = BorderRadius.circular(18);
    return Semantics(
      button: true,
      label: caption == null ? label : '$label, $caption',
      excludeSemantics: true,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: radius,
          child: Ink(
            decoration: BoxDecoration(
              borderRadius: radius,
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: highlighted
                    ? [
                        p.violet.withValues(alpha: 0.55),
                        p.sky.withValues(alpha: 0.25)
                      ]
                    : [p.surface2, p.surface1],
              ),
              border: Border.all(
                  color:
                      highlighted ? p.violet.withValues(alpha: 0.7) : p.line),
            ),
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: 96),
              child: Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: AnumSpacing.xs, vertical: AnumSpacing.sm),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    HomeIconSquare(icon: icon, tone: tone, size: 40),
                    const SizedBox(height: AnumSpacing.xs),
                    Text(label,
                        textAlign: TextAlign.center,
                        style: TextStyle(
                            color: p.text,
                            fontSize: 13,
                            fontWeight: FontWeight.w600)),
                    if (caption != null) ...[
                      const SizedBox(height: 2),
                      Text(caption!,
                          textAlign: TextAlign.center,
                          style: TextStyle(color: p.faint, fontSize: 11.5)),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Lays children out in [columns] equal columns (content-driven heights).
class HomeGrid extends StatelessWidget {
  const HomeGrid({required this.children, required this.columnsFor, super.key});
  final List<Widget> children;
  final int Function(double width) columnsFor;

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final columns = columnsFor(c.maxWidth);
        const gap = AnumSpacing.sm;
        final width = (c.maxWidth - gap * (columns - 1)) / columns;
        return Wrap(spacing: gap, runSpacing: gap, children: [
          for (final child in children) SizedBox(width: width, child: child),
        ]);
      });
}

/// A section title that screen readers announce as a heading.
class HomeSectionTitle extends StatelessWidget {
  const HomeSectionTitle(this.text, {this.subtitle, this.trailing, super.key});
  final String text;
  final String? subtitle;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Semantics(
            header: true,
            child: Text(text,
                style: TextStyle(
                    color: p.text, fontSize: 17, fontWeight: FontWeight.w700)),
          ),
          if (subtitle != null)
            Text(subtitle!, style: TextStyle(color: p.muted, fontSize: 12.5)),
        ]),
      ),
      if (trailing != null) trailing!,
    ]);
  }
}
