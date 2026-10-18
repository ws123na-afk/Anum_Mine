import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';

/// Loading, empty, error and permission states shared by the organization
/// admin screens: an icon, a title, what to do, and one next action.
class AdminStateView extends StatelessWidget {
  const AdminStateView({
    required this.icon,
    required this.title,
    required this.message,
    this.detail,
    this.action,
    this.onAction,
    super.key,
  });

  final IconData icon;
  final String title, message;

  /// The API's own words, shown under the explanation.
  final String? detail;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) => AnumPage(children: [
        AnumSurface(
          child: Column(children: [
            AnumEmpty(
                icon: icon,
                title: title,
                message: message,
                action: action,
                onAction: onAction),
            if (detail != null && detail!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.md),
                child: Text(detail!,
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.bodySmall),
              ),
          ]),
        ),
      ]);
}

/// A centred spinner announced to screen readers.
class AdminLoading extends StatelessWidget {
  const AdminLoading({required this.label, super.key});
  final String label;

  @override
  Widget build(BuildContext context) => Center(
        child: Semantics(
          label: label,
          liveRegion: true,
          child: const CircularProgressIndicator(),
        ),
      );
}

/// A short status line under a form: green for done, red for a failure.
class AdminFlash extends StatelessWidget {
  const AdminFlash({required this.text, this.error = false, super.key});
  final String text;
  final bool error;

  @override
  Widget build(BuildContext context) => Semantics(
        liveRegion: true,
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(error ? Icons.error_outline : Icons.check_circle_outline,
              size: 18,
              color:
                  anumToneColor(context, error ? AnumTone.stop : AnumTone.ok)),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
            child: Text(text,
                style: TextStyle(
                    color:
                        error ? anumToneColor(context, AnumTone.stop) : null)),
          ),
        ]),
      );
}

/// A date and time in the reader's locale, or `-`.
String adminDate(BuildContext context, DateTime? value) {
  if (value == null) return '-';
  final m = MaterialLocalizations.of(context);
  final local = value.toLocal();
  return '${m.formatMediumDate(local)} '
      '${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
}
