import 'package:flutter/material.dart';

import '../localization/anum_localizations.dart';
import '../theme/anum_theme.dart';
import 'brand.dart';

/// The greeting key for the hour of [now]: morning before 12, afternoon
/// before 18, evening after.
String greetingKey(DateTime now) => now.hour < 12
    ? 'shellGreetingMorning'
    : now.hour < 18
        ? 'shellGreetingAfternoon'
        : 'shellGreetingEvening';

/// The top of Home in the ANUM 360 mockups: the person's avatar (opens
/// Profile and settings), "Good morning" with their name and today's date,
/// and the bell with what is waiting for them.
class AnumGreetingHeader extends StatelessWidget {
  const AnumGreetingHeader({
    required this.name,
    required this.notificationCount,
    required this.onNotifications,
    required this.onProfile,
    this.subtitle,
    this.now,
    this.trailing,
    super.key,
  });

  /// The signed-in person's display name (or their user id).
  final String name;

  /// Approvals waiting for the person (the API's pending approvals).
  final int notificationCount;
  final VoidCallback onNotifications, onProfile;

  /// Under the date, for example the workspace name.
  final String? subtitle;

  /// For tests; defaults to the current time.
  final DateTime? now;

  /// Extra actions after the bell (the rail layout's refresh).
  final List<Widget>? trailing;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    final text = Theme.of(context).textTheme;
    final today = now ?? DateTime.now();
    final date = MaterialLocalizations.of(context).formatFullDate(today);
    final firstName = name.trim().split(RegExp(r'\s+')).first;
    return Row(children: [
      Semantics(
        button: true,
        label: l.t('shellOpenProfile'),
        onTap: onProfile,
        excludeSemantics: true,
        child: Tooltip(
          message: l.t('shellOpenProfile'),
          excludeFromSemantics: true,
          child: InkResponse(
            key: const Key('header-avatar'),
            onTap: onProfile,
            radius: 28,
            child: ConstrainedBox(
              constraints: const BoxConstraints(minWidth: 48, minHeight: 48),
              child: Center(child: AnumAvatar(name: name)),
            ),
          ),
        ),
      ),
      const SizedBox(width: AnumSpacing.sm),
      Expanded(
        child: Semantics(
          header: true,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(l.t(greetingKey(today)),
                  style: text.bodyMedium?.copyWith(color: p.muted)),
              Text(firstName.isEmpty ? name : firstName,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: text.titleLarge
                      ?.copyWith(fontWeight: FontWeight.w800, color: p.text)),
              Text(
                  subtitle == null || subtitle!.isEmpty
                      ? date
                      : '$date · $subtitle',
                  style: text.bodySmall?.copyWith(color: p.faint)),
            ],
          ),
        ),
      ),
      AnumNotificationBell(
        key: const Key('header-bell'),
        count: notificationCount,
        onPressed: onNotifications,
        tooltip: notificationCount == 0
            ? l.t('shellNotificationsNone')
            : l.f('shellNotificationsCount', {'count': notificationCount}),
      ),
      ...?trailing,
    ]);
  }
}
