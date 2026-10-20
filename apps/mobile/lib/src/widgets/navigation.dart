import 'package:flutter/material.dart';

import '../theme/anum_theme.dart';
import 'brand.dart';

/// One destination in [AnumBottomNav].
@immutable
class AnumNavItem {
  const AnumNavItem({
    required this.icon,
    required this.selectedIcon,
    required this.label,
    this.badge = 0,
    this.key,
  });

  final IconData icon, selectedIcon;
  final String label;

  /// A count shown on the icon (for example approvals waiting); 0 hides it.
  final int badge;
  final Key? key;
}

/// The phone's bottom navigation from the ANUM 360 mockups: two items, the
/// orb in the middle (ANUM Command), two items. [selected] is the item index
/// (0..3), [orbSelected] marks the orb's destination instead, and null
/// selects nothing. Labels that cannot fit their slot at a large text size
/// are hidden; each item keeps its tooltip and spoken label.
class AnumBottomNav extends StatelessWidget {
  const AnumBottomNav({
    required this.items,
    required this.onSelected,
    required this.onOrb,
    required this.orbLabel,
    this.selected,
    this.orbSelected = false,
    super.key,
  }) : assert(items.length == 4, 'two items on each side of the orb');

  final List<AnumNavItem> items;
  final int? selected;
  final bool orbSelected;
  final ValueChanged<int> onSelected;
  final VoidCallback onOrb;
  final String orbLabel;

  static const orbSize = 60.0;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return MediaQuery.withClampedTextScaling(
      maxScaleFactor: 1.3,
      child: Builder(builder: (context) {
        return LayoutBuilder(builder: (context, constraints) {
          final slot = (constraints.maxWidth - orbSize) / 4;
          final showLabels = _labelsFit(context, slot);
          Widget item(int i) => Expanded(
                child: _NavButton(
                  item: items[i],
                  selected: selected == i && !orbSelected,
                  showLabel: showLabels,
                  onTap: () => onSelected(i),
                ),
              );
          return DecoratedBox(
            decoration: BoxDecoration(
              color: p.glass,
              border: Border(top: BorderSide(color: p.line)),
            ),
            child: SafeArea(
              top: false,
              child: Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: AnumSpacing.xxs, vertical: AnumSpacing.xxs),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    item(0),
                    item(1),
                    _OrbButton(
                        label: orbLabel,
                        selected: orbSelected,
                        onPressed: onOrb),
                    item(2),
                    item(3),
                  ],
                ),
              ),
            ),
          );
        });
      }),
    );
  }

  bool _labelsFit(BuildContext context, double slot) {
    final style = Theme.of(context).textTheme.labelSmall;
    for (final item in items) {
      final painter = TextPainter(
        text: TextSpan(
            text: item.label,
            style: style?.copyWith(fontWeight: FontWeight.w700)),
        textDirection: Directionality.of(context),
        textScaler: MediaQuery.textScalerOf(context),
        maxLines: 1,
      )..layout();
      final fits = painter.width <= slot - AnumSpacing.xxs;
      painter.dispose();
      if (!fits) return false;
    }
    return true;
  }
}

class _NavButton extends StatelessWidget {
  const _NavButton({
    required this.item,
    required this.selected,
    required this.showLabel,
    required this.onTap,
  });

  final AnumNavItem item;
  final bool selected, showLabel;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final color = selected ? p.sky : p.muted;
    final icon =
        Icon(selected ? item.selectedIcon : item.icon, color: color, size: 24);
    return Semantics(
      key: item.key,
      button: true,
      selected: selected,
      label: item.badge > 0 ? '${item.label}, ${item.badge}' : item.label,
      onTap: onTap,
      excludeSemantics: true,
      child: Tooltip(
        message: item.label,
        excludeFromSemantics: true,
        child: InkResponse(
          onTap: onTap,
          containedInkWell: true,
          highlightShape: BoxShape.rectangle,
          borderRadius: BorderRadius.circular(AnumRadius.sm),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 56, minWidth: 48),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              mainAxisSize: MainAxisSize.min,
              children: [
                Badge(
                  isLabelVisible: item.badge > 0,
                  backgroundColor: p.stop,
                  textColor: p.background,
                  label: Text(item.badge > 99 ? '99+' : '${item.badge}'),
                  child: icon,
                ),
                if (showLabel) ...[
                  const SizedBox(height: AnumSpacing.xxs),
                  Text(item.label,
                      maxLines: 1,
                      overflow: TextOverflow.fade,
                      softWrap: false,
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: color,
                          fontWeight:
                              selected ? FontWeight.w700 : FontWeight.w600)),
                ],
                // The selected item also carries a bar, so the state is not
                // conveyed by colour alone.
                AnimatedContainer(
                  duration: AnumMotion.of(context, AnumMotion.quick),
                  curve: AnumMotion.curve,
                  margin: const EdgeInsets.only(top: AnumSpacing.xxs),
                  width: selected ? 18 : 0,
                  height: 3,
                  decoration: BoxDecoration(
                      color: p.accentStart,
                      borderRadius: BorderRadius.circular(AnumRadius.pill)),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _OrbButton extends StatelessWidget {
  const _OrbButton(
      {required this.label, required this.selected, required this.onPressed});
  final String label;
  final bool selected;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      button: true,
      selected: selected,
      label: label,
      onTap: onPressed,
      excludeSemantics: true,
      child: Tooltip(
        message: label,
        excludeFromSemantics: true,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.xxs),
          child: Material(
            type: MaterialType.circle,
            color: Colors.transparent,
            child: InkWell(
              key: const Key('nav-orb'),
              customBorder: const CircleBorder(),
              onTap: onPressed,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  boxShadow: [
                    BoxShadow(
                        color: p.glow.withValues(alpha: selected ? 0.4 : 0.22),
                        blurRadius: selected ? 16 : 10)
                  ],
                ),
                child: const AnumOrb(size: AnumBottomNav.orbSize),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// One row of [AnumMenuDrawer].
@immutable
class AnumMenuEntry {
  const AnumMenuEntry({
    required this.icon,
    required this.label,
    required this.onTap,
    this.selected = false,
    this.badge = 0,
    this.key,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final bool selected;
  final int badge;
  final Key? key;
}

/// A titled group of [AnumMenuEntry] rows.
@immutable
class AnumMenuSection {
  const AnumMenuSection({required this.entries, this.title});
  final String? title;
  final List<AnumMenuEntry> entries;
}

/// The Mobile Menu from the mockups: the wordmark, the destinations, and a
/// footer (the plan card). Closing is the caller's job in each entry's
/// [AnumMenuEntry.onTap].
class AnumMenuDrawer extends StatelessWidget {
  const AnumMenuDrawer({
    required this.sections,
    this.footer,
    this.semanticLabel,
    super.key,
  });

  final List<AnumMenuSection> sections;
  final Widget? footer;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Drawer(
      backgroundColor: p.surface1,
      semanticLabel: semanticLabel,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadiusDirectional.horizontal(
              end: Radius.circular(AnumRadius.xl))),
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.sm, vertical: AnumSpacing.md),
          children: [
            const Padding(
              padding: EdgeInsetsDirectional.fromSTEB(
                  AnumSpacing.sm, 0, AnumSpacing.sm, AnumSpacing.md),
              child: Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: AnumWordmark(markSize: 36)),
            ),
            for (final section in sections) ...[
              if (section.title != null)
                Padding(
                  padding: const EdgeInsetsDirectional.fromSTEB(AnumSpacing.sm,
                      AnumSpacing.md, AnumSpacing.sm, AnumSpacing.xxs),
                  child: Semantics(
                    header: true,
                    child: Text(section.title!,
                        style: Theme.of(context)
                            .textTheme
                            .labelMedium
                            ?.copyWith(color: p.faint)),
                  ),
                ),
              for (final entry in section.entries) _MenuTile(entry: entry),
            ],
            if (footer != null) ...[
              const SizedBox(height: AnumSpacing.md),
              footer!,
            ],
          ],
        ),
      ),
    );
  }
}

class _MenuTile extends StatelessWidget {
  const _MenuTile({required this.entry});
  final AnumMenuEntry entry;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 1),
      child: ListTile(
        key: entry.key,
        selected: entry.selected,
        selectedColor: p.violet,
        selectedTileColor: p.accentSoft,
        iconColor: p.muted,
        textColor: p.text,
        minTileHeight: 48,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AnumRadius.sm)),
        leading: Icon(entry.icon),
        title: Text(entry.label,
            style: const TextStyle(fontWeight: FontWeight.w600)),
        trailing: entry.badge > 0
            ? Badge(
                backgroundColor: p.stop,
                textColor: p.background,
                label: Text(entry.badge > 99 ? '99+' : '${entry.badge}'))
            : null,
        onTap: entry.onTap,
      ),
    );
  }
}

/// The plan card at the foot of the menu and on Profile: the workspace's
/// plan as the API reports it.
class AnumPlanCard extends StatelessWidget {
  const AnumPlanCard({
    required this.title,
    required this.subtitle,
    this.onTap,
    super.key,
  });

  final String title, subtitle;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final content = AnumGlassPanel(
      highlight: true,
      child: Row(children: [
        Icon(Icons.workspace_premium_outlined, color: p.sky),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title,
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            const SizedBox(height: 2),
            Text(subtitle,
                style: TextStyle(color: p.muted, fontSize: AnumType.xsPlus)),
          ]),
        ),
        if (onTap != null)
          Icon(
              Directionality.of(context) == TextDirection.rtl
                  ? Icons.chevron_left
                  : Icons.chevron_right,
              color: p.muted),
      ]),
    );
    if (onTap == null) return content;
    return Semantics(
      button: true,
      child: InkWell(
        borderRadius: BorderRadius.circular(AnumRadius.lg),
        onTap: onTap,
        child: content,
      ),
    );
  }
}
