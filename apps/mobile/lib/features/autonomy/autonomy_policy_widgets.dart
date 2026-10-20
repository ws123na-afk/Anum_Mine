import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../admin/admin_controller.dart' show AdminPhase;
import 'autonomy_policy_draft.dart';

/// A titled block with an optional subtitle, in the shared surface.
class PolicySection extends StatelessWidget {
  const PolicySection({
    required this.title,
    required this.children,
    this.subtitle,
    super.key,
  });
  final String title;
  final String? subtitle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
              header: true,
              child: AnumHeader(title: title, subtitle: subtitle),
            ),
            for (final child in children) ...[
              const SizedBox(height: AnumSpacing.sm),
              child,
            ],
          ],
        ),
      );
}

/// A status line announced to screen readers; red for a failure, green else.
class PolicyNotice extends StatelessWidget {
  const PolicyNotice(
      {required this.text, required this.failed, this.noticeKey, super.key});
  final String text;
  final bool failed;
  final Key? noticeKey;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      liveRegion: true,
      child: Text(
        text,
        key: noticeKey,
        style: Theme.of(context)
            .textTheme
            .bodyMedium
            ?.copyWith(color: failed ? p.stop : p.ok),
      ),
    );
  }
}

/// The loading, access-denied and failure states of a policy screen.
List<Widget> policyPhaseState(
  BuildContext context,
  AdminPhase phase,
  String? message,
  String what,
  VoidCallback retry,
) {
  final p = context.palette;
  final l = AnumLocalizations.maybeOf(context);
  return switch (phase) {
    AdminPhase.initial || AdminPhase.loading => [
        Padding(
          padding: const EdgeInsets.all(AnumSpacing.lg),
          child: Center(
            child: Semantics(
              label: l.t('da.loading'),
              child: const CircularProgressIndicator(),
            ),
          ),
        ),
      ],
    AdminPhase.forbidden => [
        AnumSurface(
          key: Key('$what-forbidden'),
          accent: p.warn,
          child: AnumEmpty(
            icon: Icons.lock_outline,
            title: l.t('auto.accessRequired'),
            message: l.f('ap.accessMessage',
                {'what': l.t('ap.what.$what'), 'message': message ?? ''}),
            action: l.t('da.retry'),
            onAction: retry,
          ),
        ),
      ],
    _ => [
        AnumSurface(
          key: Key('$what-failed'),
          accent: p.stop,
          child: AnumEmpty(
            icon: phase == AdminPhase.offline
                ? Icons.cloud_off_outlined
                : Icons.error_outline,
            title:
                l.f('auto.loadFailed', {'what': l.t('ap.what.${what}Title')}),
            message: l.f('auto.checkConnection', {'message': message ?? ''}),
            action: l.t('da.retry'),
            onAction: retry,
          ),
        ),
      ],
  };
}

/// Quiet hours: a switch and start, end and time zone.
class QuietHoursEditor extends StatelessWidget {
  const QuietHoursEditor({
    required this.draft,
    required this.readOnly,
    required this.onChanged,
    super.key,
  });
  final PolicyDraft draft;
  final bool readOnly;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final muted = Theme.of(context).textTheme.bodySmall;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SwitchListTile(
          key: const Key('quiet-on'),
          contentPadding: EdgeInsets.zero,
          title: Text(l.t('ap.quietOn')),
          value: draft.quietOn,
          onChanged: readOnly
              ? null
              : (v) {
                  draft.quietOn = v;
                  onChanged();
                },
        ),
        if (!draft.quietOn)
          Text(l.t('ap.quietOff'), key: const Key('quiet-off'), style: muted)
        else ...[
          const SizedBox(height: AnumSpacing.xs),
          TextFormField(
            key: const Key('quiet-start'),
            initialValue: draft.quietStart,
            enabled: !readOnly,
            keyboardType: TextInputType.datetime,
            decoration: InputDecoration(
              labelText: l.t('ap.quietStart'),
              helperText: l.t('ap.quietTimeHelp'),
            ),
            onChanged: (v) => draft.quietStart = v,
          ),
          const SizedBox(height: AnumSpacing.sm),
          TextFormField(
            key: const Key('quiet-end'),
            initialValue: draft.quietEnd,
            enabled: !readOnly,
            keyboardType: TextInputType.datetime,
            decoration: InputDecoration(
              labelText: l.t('ap.quietEnd'),
              helperText: l.t('ap.quietTimeHelp'),
            ),
            onChanged: (v) => draft.quietEnd = v,
          ),
          const SizedBox(height: AnumSpacing.sm),
          TextFormField(
            key: const Key('quiet-zone'),
            initialValue: draft.quietZone,
            enabled: !readOnly,
            autocorrect: false,
            decoration: InputDecoration(
              labelText: l.t('ap.quietZone'),
              helperText: l.t('ap.quietZoneHelp'),
              helperMaxLines: 3,
            ),
            onChanged: (v) => draft.quietZone = v,
          ),
        ],
      ],
    );
  }
}

/// Daily caps: rows of name and number, add and remove.
class CapsEditor extends StatelessWidget {
  const CapsEditor({
    required this.draft,
    required this.readOnly,
    required this.onChanged,
    super.key,
  });
  final PolicyDraft draft;
  final bool readOnly;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final muted = Theme.of(context).textTheme.bodySmall;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (draft.caps.isEmpty)
          Text(
            readOnly ? l.t('ap.capsNone') : l.t('ap.capsEmpty'),
            key: const Key('caps-empty'),
            style: muted,
          ),
        for (var i = 0; i < draft.caps.length; i++) ...[
          if (i > 0) const SizedBox(height: AnumSpacing.md),
          _CapRowEditor(
            key: ObjectKey(draft.caps[i]),
            row: draft.caps[i],
            index: i,
            readOnly: readOnly,
            onRemove: () {
              draft.removeCap(draft.caps[i]);
              onChanged();
            },
          ),
        ],
        if (!readOnly) ...[
          const SizedBox(height: AnumSpacing.sm),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('cap-add'),
              onPressed: () {
                draft.addCap();
                onChanged();
              },
              icon: const Icon(Icons.add),
              label: Text(l.t('ap.capAdd')),
            ),
          ),
        ],
      ],
    );
  }
}

class _CapRowEditor extends StatelessWidget {
  const _CapRowEditor({
    required this.row,
    required this.index,
    required this.readOnly,
    required this.onRemove,
    super.key,
  });
  final CapRow row;
  final int index;
  final bool readOnly;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextFormField(
          key: Key('cap-key-$index'),
          initialValue: row.key,
          enabled: !readOnly,
          autocorrect: false,
          decoration: InputDecoration(
            labelText: l.t('ap.capKey'),
            helperText: l.t('ap.capKeyHelp'),
            helperMaxLines: 2,
          ),
          onChanged: (v) => row.key = v,
        ),
        const SizedBox(height: AnumSpacing.xs),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: TextFormField(
                key: Key('cap-value-$index'),
                initialValue: row.value,
                enabled: !readOnly,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(labelText: l.t('ap.capValue')),
                onChanged: (v) => row.value = v,
              ),
            ),
            if (!readOnly)
              IconButton(
                key: Key('cap-remove-$index'),
                tooltip: row.key.trim().isEmpty
                    ? l.t('ap.capRemoveRow')
                    : l.f('ap.capRemove', {'key': row.key.trim()}),
                onPressed: onRemove,
                icon: const Icon(Icons.delete_outline),
              ),
          ],
        ),
      ],
    );
  }
}

/// A list of strings with add, remove, and (past 20 entries) search and
/// "Show more". The caller validates in [onAdd], which returns a problem or
/// null.
class StringListEditor extends StatefulWidget {
  const StringListEditor({
    required this.id,
    required this.items,
    required this.readOnly,
    required this.fieldLabel,
    required this.emptyText,
    required this.onAdd,
    required this.onRemove,
    super.key,
  });
  final String id;
  final List<String> items;
  final bool readOnly;
  final String fieldLabel, emptyText;
  final String? Function(String value) onAdd;
  final void Function(String value) onRemove;

  @override
  State<StringListEditor> createState() => _StringListEditorState();
}

class _StringListEditorState extends State<StringListEditor> {
  static const _page = 20;
  final _input = TextEditingController();
  String? _error;
  String _query = '';
  int _visible = _page;

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  void _add() {
    final problem = widget.onAdd(_input.text);
    setState(() => _error = problem);
    if (problem == null) _input.clear();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final theme = Theme.of(context).textTheme;
    final q = _query.trim().toLowerCase();
    final matching = [
      for (final i in widget.items)
        if (q.isEmpty || i.toLowerCase().contains(q)) i,
    ];
    final shown = matching.take(_visible).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!widget.readOnly)
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: TextField(
                  key: Key('${widget.id}-input'),
                  controller: _input,
                  autocorrect: false,
                  keyboardType: TextInputType.emailAddress,
                  decoration: InputDecoration(
                    labelText: widget.fieldLabel,
                    errorText: _error,
                    errorMaxLines: 3,
                  ),
                  onSubmitted: (_) => _add(),
                ),
              ),
              IconButton.filled(
                key: Key('${widget.id}-add'),
                tooltip: l.t('ap.addItem'),
                onPressed: _add,
                icon: const Icon(Icons.add),
              ),
            ],
          ),
        if (widget.items.length > _page) ...[
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: Key('${widget.id}-search'),
            decoration: InputDecoration(
              labelText: l.t('ap.filterList'),
              prefixIcon: const Icon(Icons.search),
            ),
            onChanged: (v) => setState(() {
              _query = v;
              _visible = _page;
            }),
          ),
        ],
        const SizedBox(height: AnumSpacing.xs),
        if (widget.items.isEmpty)
          Text(widget.emptyText,
              key: Key('${widget.id}-empty'), style: theme.bodySmall)
        else if (matching.isEmpty)
          Text(l.f('ap.noMatch', {'q': _query.trim()}), style: theme.bodySmall)
        else
          for (final item in shown)
            Row(
              key: ValueKey('${widget.id}-$item'),
              children: [
                Expanded(
                  child: Text(item, style: theme.bodyMedium),
                ),
                if (!widget.readOnly)
                  IconButton(
                    tooltip: l.f('ap.removeItem', {'item': item}),
                    onPressed: () {
                      widget.onRemove(item);
                      setState(() {});
                    },
                    icon: Icon(Icons.close, color: p.muted),
                  ),
              ],
            ),
        if (matching.length > shown.length)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton(
              key: Key('${widget.id}-more'),
              onPressed: () => setState(() => _visible += _page),
              child: Text(l.f('ap.showMore',
                  {'shown': shown.length, 'total': matching.length})),
            ),
          ),
      ],
    );
  }
}
