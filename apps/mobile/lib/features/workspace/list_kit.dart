import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'workspace_models.dart';

/// Building blocks of the ANUM 360 list screens (Agents, Tasks, Automations,
/// Integrations; docs/design/anum360.md): a title row with a create action,
/// pill tabs with counts, rows in one raised card, priority pills and the
/// gradient create button. Kept in this feature folder while the shared
/// widget files are being reworked; every value comes from the tokens and
/// the gradient is the shell's own.

/// The shell's accent gradient (`AnumPalette.accentGradient`) of primary
/// actions and selected tabs.
LinearGradient anumAccentGradient(BuildContext context) =>
    context.palette.accentGradient;

/// Text and icons on [anumAccentGradient] (`AnumPalette.onAccent`).
Color anumOnAccent(BuildContext context) => context.palette.onAccent;

/// Large title, a muted line under it, and the screen's create action.
class AnumListHeader extends StatelessWidget {
  const AnumListHeader(
      {required this.title,
      this.subtitle,
      this.actionTooltip,
      this.onAction,
      this.actionIcon = Icons.add,
      this.trailing,
      super.key});
  final String title;
  final String? subtitle;
  final String? actionTooltip;
  final VoidCallback? onAction;
  final IconData actionIcon;

  /// Extra controls before the create action (for example search).
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Semantics(
            header: true,
            child: Text(title,
                style: text.headlineSmall
                    ?.copyWith(color: p.text, fontWeight: FontWeight.w800)),
          ),
          if (subtitle != null) ...[
            const SizedBox(height: AnumSpacing.xxs),
            Text(subtitle!, style: text.bodyMedium?.copyWith(color: p.muted)),
          ],
        ]),
      ),
      if (trailing != null) trailing!,
      if (onAction != null) ...[
        const SizedBox(width: AnumSpacing.xs),
        AnumGradientIconButton(
            icon: actionIcon,
            tooltip: actionTooltip ?? '',
            onPressed: onAction!),
      ],
    ]);
  }
}

/// A round gradient icon button (the mockups' "+"), 48 by 48.
class AnumGradientIconButton extends StatelessWidget {
  const AnumGradientIconButton(
      {required this.icon,
      required this.tooltip,
      required this.onPressed,
      this.size = 48,
      super.key});
  final IconData icon;
  final String tooltip;
  final VoidCallback onPressed;
  final double size;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Tooltip(
      message: tooltip,
      child: Semantics(
        button: true,
        label: tooltip,
        excludeSemantics: true,
        child: Material(
          type: MaterialType.transparency,
          child: InkResponse(
            onTap: onPressed,
            radius: size / 2,
            child: Container(
              width: size,
              height: size,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: anumAccentGradient(context),
                boxShadow: [
                  BoxShadow(
                      color: p.violet.withValues(alpha: 0.35),
                      blurRadius: AnumSpacing.md,
                      offset: const Offset(0, AnumSpacing.xxs)),
                ],
              ),
              child: Icon(icon, color: anumOnAccent(context)),
            ),
          ),
        ),
      ),
    );
  }
}

/// The floating create button of a list (Tasks).
class AnumGradientFab extends StatelessWidget {
  const AnumGradientFab(
      {required this.tooltip, required this.onPressed, super.key});
  final String tooltip;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => AnumGradientIconButton(
      icon: Icons.add, tooltip: tooltip, onPressed: onPressed, size: 56);
}

/// One pill tab.
class AnumSegment<T> {
  const AnumSegment(this.value, this.label, {this.count, this.key});
  final T value;
  final String label;
  final int? count;
  final Key? key;
}

/// Pill tabs (All / Work / Personal ...): the selected one filled with the
/// accent gradient. Scrolls sideways when the labels do not fit; every pill
/// keeps a 48 dp target and announces its selection.
class AnumSegmentTabs<T> extends StatelessWidget {
  const AnumSegmentTabs(
      {required this.segments,
      required this.selected,
      required this.onSelected,
      super.key});
  final List<AnumSegment<T>> segments;
  final T selected;
  final ValueChanged<T> onSelected;

  @override
  Widget build(BuildContext context) => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(children: [
          for (final segment in segments)
            Padding(
              padding: const EdgeInsetsDirectional.only(end: AnumSpacing.xs),
              child: _SegmentPill(
                  key: segment.key,
                  label: segment.count == null
                      ? segment.label
                      : '${segment.label} ${segment.count}',
                  spoken: segment.count == null
                      ? segment.label
                      : '${segment.label}, ${segment.count}',
                  selected: segment.value == selected,
                  onTap: () => onSelected(segment.value)),
            ),
        ]),
      );
}

class _SegmentPill extends StatelessWidget {
  const _SegmentPill(
      {required this.label,
      required this.spoken,
      required this.selected,
      required this.onTap,
      super.key});
  final String label, spoken;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final style = Theme.of(context).textTheme.labelLarge?.copyWith(
        color: selected ? anumOnAccent(context) : p.muted,
        fontWeight: selected ? FontWeight.w800 : FontWeight.w600);
    return Semantics(
      button: true,
      selected: selected,
      label: spoken,
      excludeSemantics: true,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(999),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 48, minWidth: 48),
          child: Center(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: selected ? anumAccentGradient(context) : null,
                color: selected ? null : p.surface2,
                borderRadius: BorderRadius.circular(999),
                border: Border.all(color: selected ? p.edge : p.line),
              ),
              child: Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
                child: Text(label, style: style),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Rows in one raised card, separated by hairlines.
class AnumListCard extends StatelessWidget {
  const AnumListCard({required this.children, this.footer, super.key});
  final List<Widget> children;
  final Widget? footer;

  @override
  Widget build(BuildContext context) => AnumSurface(
        padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.sm, vertical: AnumSpacing.xxs),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) const Divider(height: 1),
            children[i],
          ],
          if (footer != null) ...[
            if (children.isNotEmpty) const Divider(height: 1),
            footer!,
          ],
        ]),
      );
}

/// A square tinted icon at the start of a row; the colour names the kind.
class AnumTileIcon extends StatelessWidget {
  const AnumTileIcon({required this.icon, required this.color, super.key});
  final IconData icon;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        width: 40,
        height: 40,
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.18),
          borderRadius: BorderRadius.circular(AnumSpacing.sm),
          border: Border.all(color: color.withValues(alpha: 0.32)),
        ),
        child: Icon(icon, size: 22, color: color),
      );
}

/// A row: leading icon, title and details, trailing control, chevron when
/// it opens something. Wraps at large text sizes instead of overflowing.
class AnumListRow extends StatelessWidget {
  const AnumListRow(
      {required this.title,
      this.leading,
      this.subtitle,
      this.detail,
      this.trailing,
      this.onTap,
      this.chevron = true,
      this.semanticLabel,
      super.key});
  final String title;
  final Widget? leading;
  final String? subtitle;

  /// A second line under the subtitle: pills, dates.
  final Widget? detail;
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool chevron;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    // A narrow row at large text puts the trailing control under the text
    // instead of squeezing the title to nothing.
    final body = LayoutBuilder(builder: (context, c) {
      final scale = MediaQuery.textScalerOf(context).scale(1);
      final stacked = trailing != null &&
          (c.maxWidth < 300 || (scale > 1.4 && c.maxWidth < 440));
      return ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 64),
        child: Padding(
          padding: const EdgeInsets.symmetric(
              vertical: AnumSpacing.sm, horizontal: AnumSpacing.xxs),
          child: Row(children: [
            if (leading != null) ...[
              leading!,
              const SizedBox(width: AnumSpacing.sm),
            ],
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: text.titleSmall?.copyWith(
                            color: p.text, fontWeight: FontWeight.w700)),
                    if (subtitle != null && subtitle!.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(subtitle!,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: text.bodySmall?.copyWith(color: p.muted)),
                    ],
                    if (detail != null) ...[
                      const SizedBox(height: AnumSpacing.xxs),
                      detail!,
                    ],
                    if (stacked) ...[
                      const SizedBox(height: AnumSpacing.xs),
                      trailing!,
                    ],
                  ]),
            ),
            if (trailing != null && !stacked) ...[
              const SizedBox(width: AnumSpacing.xs),
              trailing!,
            ],
            if (onTap != null && chevron)
              Icon(Icons.chevron_right, color: p.faint, size: 22),
          ]),
        ),
      );
    });
    if (onTap == null) {
      return Semantics(
          container: true,
          label: semanticLabel,
          excludeSemantics: semanticLabel != null && trailing == null,
          child: body);
    }
    return Semantics(
      button: true,
      label: semanticLabel,
      child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(AnumSpacing.sm),
          child: body),
    );
  }
}

/// High / Medium / Low, coloured and spelled out.
class PriorityPill extends StatelessWidget {
  const PriorityPill(this.priority, {super.key});
  final TaskPriority priority;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return AnumPill(
        label: l.t(priorityKey(priority)), tone: priorityTone(priority));
  }
}

String priorityKey(TaskPriority priority) => switch (priority) {
      TaskPriority.high => 'priorityHigh',
      TaskPriority.medium => 'priorityMedium',
      TaskPriority.low => 'priorityLow',
    };

AnumTone priorityTone(TaskPriority priority) => switch (priority) {
      TaskPriority.high => AnumTone.stop,
      TaskPriority.medium => AnumTone.warn,
      TaskPriority.low => AnumTone.ok,
    };

/// A centred spinner with a spoken label, for a list's first load.
class AnumListLoading extends StatelessWidget {
  const AnumListLoading({required this.label, super.key});
  final String label;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.all(AnumSpacing.xl),
        child: Center(child: CircularProgressIndicator(semanticsLabel: label)),
      );
}

/// "Load more" under a paged list.
class AnumLoadMore extends StatelessWidget {
  const AnumLoadMore(
      {required this.loading, required this.onPressed, super.key});
  final bool loading;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Center(
      child: OutlinedButton.icon(
        onPressed: loading ? null : onPressed,
        icon: const Icon(Icons.expand_more),
        label: Text(loading ? l.t('listLoading') : l.t('listLoadMore')),
      ),
    );
  }
}
