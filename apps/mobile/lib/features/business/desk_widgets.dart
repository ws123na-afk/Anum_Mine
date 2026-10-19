import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'business_controller.dart';

/// Pieces shared by the Business editing screens (catalog, accounts and
/// contacts): a paged list with every state, a confirmation for destructive
/// changes and a reason prompt.

/// A paged list with its loading, permission, error, empty and "load more"
/// states, so each Business section only says how a row looks.
class DeskList<T> extends StatelessWidget {
  const DeskList({
    required this.state,
    required this.items,
    required this.total,
    required this.loadingMore,
    required this.error,
    required this.onRetry,
    required this.onMore,
    required this.row,
    required this.emptyTitle,
    required this.emptyText,
    this.filtered = false,
    this.noMatchText,
    this.emptyAction,
    this.onEmptyAction,
    super.key,
  });

  final BusinessLoad state;
  final List<T> items;
  final int total;
  final bool loadingMore, filtered;
  final String? error, noMatchText, emptyAction;
  final VoidCallback onRetry, onMore;
  final VoidCallback? onEmptyAction;
  final Widget Function(T item) row;
  final String emptyTitle, emptyText;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    switch (state) {
      case BusinessLoad.idle:
      case BusinessLoad.loading:
        if (items.isEmpty) {
          return AnumFeedback(
            kind: AnumFeedbackKind.loading,
            message: l.t('biz.loading'),
          );
        }
      case BusinessLoad.denied:
        return AnumFeedback(
          kind: AnumFeedbackKind.permission,
          message: l.t('biz.denied'),
        );
      case BusinessLoad.error:
        return AnumFeedback(
          kind: AnumFeedbackKind.error,
          message: l.f('biz.failed', {'message': error ?? ''}),
          onRetry: onRetry,
        );
      case BusinessLoad.ready:
        break;
    }
    if (items.isEmpty) {
      return AnumEmpty(
        title: filtered ? l.t('biz.form.noMatchTitle') : emptyTitle,
        message:
            filtered ? (noMatchText ?? l.t('biz.form.noMatch')) : emptyText,
        action: filtered ? null : emptyAction,
        onAction: filtered ? null : onEmptyAction,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final item in items) AnumSurface(child: row(item)),
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Text(error!, style: TextStyle(color: context.palette.stop)),
          ),
        if (items.length < total)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: OutlinedButton(
              key: const Key('desk-more'),
              onPressed: loadingMore ? null : onMore,
              child: Text(
                l.f('biz.more', {'shown': items.length, 'total': total}),
              ),
            ),
          ),
      ],
    );
  }
}

/// Asks before a destructive change; true when the person confirmed.
Future<bool> confirmDestructive(
  BuildContext context, {
  required String title,
  required String message,
  required String action,
}) async {
  final l = context.anum;
  final ok = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: Text(l.t('biz.cancel')),
        ),
        FilledButton(
          key: const Key('desk-confirm'),
          onPressed: () => Navigator.pop(context, true),
          child: Text(action),
        ),
      ],
    ),
  );
  return ok ?? false;
}

/// Asks for a short reason (at least three characters, as the server wants).
Future<String?> askReason(
  BuildContext context, {
  required String title,
  required String label,
}) =>
    showDialog<String>(
      context: context,
      builder: (context) => _ReasonPrompt(title: title, label: label),
    );

class _ReasonPrompt extends StatefulWidget {
  const _ReasonPrompt({required this.title, required this.label});
  final String title, label;

  @override
  State<_ReasonPrompt> createState() => _ReasonPromptState();
}

class _ReasonPromptState extends State<_ReasonPrompt> {
  final reason = TextEditingController();

  @override
  void dispose() {
    reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(widget.title),
      content: TextField(
        key: const Key('desk-reason'),
        controller: reason,
        autofocus: true,
        maxLength: 300,
        decoration: InputDecoration(labelText: widget.label),
        onChanged: (_) => setState(() {}),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l.t('biz.cancel')),
        ),
        FilledButton(
          key: const Key('desk-reason-ok'),
          onPressed: reason.text.trim().length < 3
              ? null
              : () => Navigator.pop(context, reason.text.trim()),
          child: Text(l.t('biz.confirm')),
        ),
      ],
    );
  }
}

/// A form screen's scaffold: title, the server's refusal under it, the fields
/// and one save button that shows progress. Content-driven height.
class DeskForm extends StatelessWidget {
  const DeskForm({
    required this.formKey,
    required this.title,
    required this.children,
    required this.onSave,
    required this.busy,
    this.error,
    this.saveLabel,
    super.key,
  });
  final GlobalKey<FormState> formKey;
  final String title;
  final List<Widget> children;
  final VoidCallback onSave;
  final bool busy;
  final String? error, saveLabel;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: Form(
        key: formKey,
        child: AnumPage(
          children: [
            if (error != null)
              Semantics(
                liveRegion: true,
                child: Text(
                  error!,
                  key: const Key('desk-form-error'),
                  style: TextStyle(color: context.palette.stop),
                ),
              ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: children,
              ),
            ),
            FilledButton(
              key: const Key('desk-save'),
              onPressed: busy ? null : onSave,
              child: busy
                  ? const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Text(saveLabel ?? l.t('biz.form.save')),
            ),
          ],
        ),
      ),
    );
  }
}

/// A labelled dropdown over a fixed list of server values, shown through
/// [label] so every value reads in the app's language.
Widget deskChoice({
  required Key key,
  required String label,
  required String value,
  required List<String> values,
  required String Function(String value) text,
  required ValueChanged<String> onChanged,
}) =>
    Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.sm),
      child: DropdownButtonFormField<String>(
        key: key,
        isExpanded: true,
        initialValue: value,
        decoration: InputDecoration(labelText: label),
        items: [
          for (final item in values)
            DropdownMenuItem(value: item, child: Text(text(item))),
        ],
        onChanged: (next) {
          if (next != null) onChanged(next);
        },
      ),
    );

/// Splits a multi-line field into its non-empty trimmed lines.
List<String> deskLines(String text) => [
      for (final line in text.split('\n'))
        if (line.trim().isNotEmpty) line.trim(),
    ];

/// The number in a form field, or null when empty; NaN-safe.
double? deskNumber(String text) => double.tryParse(text.trim());
