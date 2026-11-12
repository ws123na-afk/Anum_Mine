import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';

/// Small pieces shared by the owner desk screens (Executive desk, Ready to
/// sell, IBKR trading, Action browser), built on the depth widgets so they
/// look like the rest of the app.

/// [AnumLocalizations.f] with an empty map falling back to [t].
String deskText(AnumLocalizations l, String key,
        [Map<String, Object> values = const {}]) =>
    values.isEmpty ? l.t(key) : l.f(key, values);

/// A date and time in the page language, or `-`.
String deskWhen(BuildContext context, DateTime? at) {
  if (at == null) return '-';
  final local = at.toLocal();
  final m = MaterialLocalizations.of(context);
  return '${m.formatMediumDate(local)} '
      '${m.formatTimeOfDay(TimeOfDay.fromDateTime(local), alwaysUse24HourFormat: MediaQuery.alwaysUse24HourFormatOf(context))}';
}

/// A one-line status message that screen readers announce.
class DeskMessage extends StatelessWidget {
  const DeskMessage({required this.text, this.failed = false, super.key});
  final String text;
  final bool failed;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final color = failed ? p.stop : p.ok;
    return Semantics(
      liveRegion: true,
      child: Container(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(AnumRadius.lg),
          border: Border.all(color: color.withValues(alpha: 0.4)),
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(failed ? Icons.error_outline : Icons.check_circle_outline,
              size: 18, color: color),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
              child: Text(text,
                  style: TextStyle(color: p.text, fontSize: AnumType.sm))),
        ]),
      ),
    );
  }
}

/// A centred spinner with a spoken label while a part of a page loads.
class DeskLoading extends StatelessWidget {
  const DeskLoading({required this.label, super.key});
  final String label;

  @override
  Widget build(BuildContext context) => Semantics(
        label: label,
        liveRegion: true,
        child: const Padding(
          padding: EdgeInsets.all(AnumSpacing.lg),
          child: Center(child: CircularProgressIndicator()),
        ),
      );
}

/// A label and a value on one line that wraps at large text.
class DeskFact extends StatelessWidget {
  const DeskFact(
      {required this.label, required this.value, this.ltr = false, super.key});
  final String label;
  final String value;

  /// Addresses, codes and hashes read left to right in Arabic too.
  final bool ltr;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
      child: Wrap(
          spacing: AnumSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(label,
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            Text(value,
                textDirection: ltr ? TextDirection.ltr : null,
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.sm,
                    fontWeight: FontWeight.w700)),
          ]),
    );
  }
}

/// A heading inside a page with an optional count.
class DeskSectionTitle extends StatelessWidget {
  const DeskSectionTitle(
      {required this.title, this.count, this.icon, super.key});
  final String title;
  final int? count;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      header: true,
      child: Row(children: [
        if (icon != null) ...[
          Icon(icon, size: 18, color: p.muted),
          const SizedBox(width: AnumSpacing.xs),
        ],
        Flexible(
          child: Text(title,
              style: TextStyle(
                  color: p.text,
                  fontSize: AnumType.base,
                  fontWeight: FontWeight.w800)),
        ),
        if (count != null) ...[
          const SizedBox(width: AnumSpacing.xs),
          AnumPill(label: '$count'),
        ],
      ]),
    );
  }
}

/// Lays children out one per row on phones and two per row from 760 px.
class DeskColumns extends StatelessWidget {
  const DeskColumns({required this.children, super.key});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        if (c.maxWidth < 760 || children.length < 2) {
          return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (var i = 0; i < children.length; i++) ...[
                  if (i > 0) const SizedBox(height: AnumSpacing.sm),
                  children[i],
                ]
              ]);
        }
        final rows = <Widget>[];
        for (var i = 0; i < children.length; i += 2) {
          if (i > 0) rows.add(const SizedBox(height: AnumSpacing.sm));
          rows.add(IntrinsicHeight(
            child:
                Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Expanded(child: children[i]),
              const SizedBox(width: AnumSpacing.sm),
              Expanded(
                  child: i + 1 < children.length
                      ? children[i + 1]
                      : const SizedBox.shrink()),
            ]),
          ));
        }
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch, children: rows);
      });
}

/// Asks before a change that cannot be undone from this screen; true when
/// the person confirms.
Future<bool> deskConfirm(BuildContext context,
    {required String title,
    required String text,
    required String confirm,
    required String cancel,
    bool danger = false,
    Widget? extra,
    Key? confirmKey}) async {
  final p = context.palette;
  final ok = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: SingleChildScrollView(
        child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(text),
              if (extra != null) ...[
                const SizedBox(height: AnumSpacing.sm),
                extra,
              ],
            ]),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(cancel)),
        FilledButton(
            key: confirmKey,
            style: danger
                ? FilledButton.styleFrom(
                    backgroundColor: p.stop, foregroundColor: p.background)
                : null,
            onPressed: () => Navigator.pop(context, true),
            child: Text(confirm)),
      ],
    ),
  );
  return ok == true;
}

/// A payload hash shown in full, left to right, selectable.
class DeskHash extends StatelessWidget {
  const DeskHash({required this.label, required this.hash, super.key});
  final String label, hash;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
      const SizedBox(height: AnumSpacing.xxs),
      SelectableText(hash,
          textDirection: TextDirection.ltr,
          style: TextStyle(
              color: p.text,
              fontSize: AnumType.xs,
              fontFamily: 'monospace',
              fontFamilyFallback: const ['Courier'])),
    ]);
  }
}

/// A text field for a dialog that owns its controller, so the controller
/// lives exactly as long as the dialog (closing animations included).
class DeskTextField extends StatefulWidget {
  const DeskTextField(
      {required this.label,
      required this.onChanged,
      this.maxLength,
      this.ltr = false,
      this.initial = '',
      this.number = false,
      super.key});
  final String label;
  final ValueChanged<String> onChanged;
  final int? maxLength;
  final bool ltr;

  /// The text the field starts with.
  final String initial;

  /// A decimal number: the number keyboard, left to right.
  final bool number;

  @override
  State<DeskTextField> createState() => _DeskTextFieldState();
}

class _DeskTextFieldState extends State<DeskTextField> {
  late final _text = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => TextField(
        controller: _text,
        maxLength: widget.maxLength,
        autocorrect: !widget.ltr && !widget.number,
        keyboardType: widget.number
            ? const TextInputType.numberWithOptions(decimal: true)
            : null,
        textDirection: widget.ltr || widget.number ? TextDirection.ltr : null,
        onChanged: widget.onChanged,
        decoration: InputDecoration(labelText: widget.label),
      );
}
