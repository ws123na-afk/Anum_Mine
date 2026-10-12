import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'command_controller.dart';
import 'command_models.dart';
import 'command_strings.dart';

/// Quick commands and templates (Work, Personal, Custom): the person's saved
/// requests, run with one tap (docs/command.md#quick-commands).
class QuickCommandsScreen extends StatefulWidget {
  const QuickCommandsScreen({required this.controller, super.key});
  final CommandController controller;

  @override
  State<QuickCommandsScreen> createState() => _QuickCommandsScreenState();
}

class _QuickCommandsScreenState extends State<QuickCommandsScreen> {
  CommandController get c => widget.controller;
  QuickCategory _category = QuickCategory.work;

  @override
  void initState() {
    super.initState();
    unawaited(c.loadQuick());
  }

  String _categoryLabel(CommandStrings s, QuickCategory category) =>
      switch (category) {
        QuickCategory.work => s.t('work'),
        QuickCategory.personal => s.t('personal'),
        QuickCategory.custom => s.t('custom'),
      };

  void _snack(String text) => ScaffoldMessenger.maybeOf(context)
      ?.showSnackBar(SnackBar(content: Text(text)));

  String _failureText(CommandStrings s, CommandFailure failure) =>
      switch (failure.kind) {
        FailureKind.permission => s.t('quickPermission'),
        FailureKind.offline => s.t('offline'),
        _ => failure.message,
      };

  Future<void> _edit([QuickCommand? existing]) async {
    final s = CommandStrings.of(context);
    final result = await showDialog<_QuickDraft>(
      context: context,
      builder: (_) => _QuickDialog(
        strings: s,
        existing: existing,
        category: existing?.category ?? _category,
        categoryLabel: (value) => _categoryLabel(s, value),
      ),
    );
    if (result == null) return;
    final failure = await c.saveQuick(
        existing: existing,
        title: result.title,
        text: result.text,
        category: result.category);
    if (!mounted) return;
    if (failure != null) {
      _snack(_failureText(s, failure));
    } else {
      setState(() => _category = result.category);
    }
  }

  Future<void> _delete(QuickCommand item) async {
    final s = CommandStrings.of(context);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        content: Text(s.f('deleteQuickConfirm', {'title': item.title})),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(s.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: Text(s.t('delete'))),
        ],
      ),
    );
    if (confirmed != true) return;
    final failure = await c.deleteQuick(item);
    if (failure != null && mounted) _snack(_failureText(s, failure));
  }

  Future<void> _move(QuickCommand item, int delta) async {
    final s = CommandStrings.of(context);
    final failure = await c.moveQuick(item, delta);
    if (failure != null && mounted) _snack(_failureText(s, failure));
  }

  void _run(QuickCommand item) {
    unawaited(c.startFrom(item.text,
        source: 'quick_command', quickCommandId: item.id));
    Navigator.of(context).maybePop();
  }

  @override
  Widget build(BuildContext context) {
    final s = CommandStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('quickTitle'))),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _edit(),
        icon: const Icon(Icons.add),
        label: Text(s.t('addQuick')),
      ),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final width = MediaQuery.sizeOf(context).width;
          final gutter =
              width < AnumBreakpoints.compact ? AnumSpacing.md : AnumSpacing.lg;
          final items = c.quickIn(_category);
          return ListView(
            padding: EdgeInsets.fromLTRB(gutter, AnumSpacing.sm, gutter, 96),
            children: [
              Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 720),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      SegmentedButton<QuickCategory>(
                        expandedInsets: EdgeInsets.zero,
                        segments: [
                          for (final category in QuickCategory.values)
                            ButtonSegment(
                                value: category,
                                label: Text(_categoryLabel(s, category),
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    textAlign: TextAlign.center)),
                        ],
                        selected: {_category},
                        showSelectedIcon: false,
                        onSelectionChanged: (value) =>
                            setState(() => _category = value.first),
                      ),
                      const SizedBox(height: AnumSpacing.md),
                      ..._content(s, items),
                    ],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  List<Widget> _content(CommandStrings s, List<QuickCommand> items) {
    if (c.quickPhase == LoadPhase.loading && c.quick.isEmpty) {
      return const [
        Center(
            child: Padding(
                padding: EdgeInsets.all(AnumSpacing.xl),
                child: CircularProgressIndicator()))
      ];
    }
    if (c.quickPhase == LoadPhase.error) {
      final failure = c.quickError!;
      return [
        AnumEmpty(
          icon: failure.kind == FailureKind.permission
              ? Icons.lock_outline
              : Icons.error_outline,
          title: s.t('quickTitle'),
          message: _failureText(s, failure),
          action: s.t('retry'),
          onAction: c.loadQuick,
        )
      ];
    }
    if (items.isEmpty) {
      return [
        AnumEmpty(
          icon: Icons.bolt_outlined,
          title: s.f('quickEmpty', {'category': _categoryLabel(s, _category)}),
          message: s.t('quickEmptyHint'),
          action: s.t('addQuick'),
          onAction: () => _edit(),
        )
      ];
    }
    return [
      for (var i = 0; i < items.length; i++) ...[
        AnumSurface(
          padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.sm),
          child: AnumRow(
            icon: Icons.bolt_outlined,
            tone: AnumTone.info,
            title: items[i].title,
            subtitle: items[i].text,
            detail: items[i].useCount > 0
                ? s.f('usedTimes', {'count': items[i].useCount})
                : null,
            onTap: () => _run(items[i]),
            trailing: PopupMenuButton<String>(
              tooltip: s.f('moreActions', {'title': items[i].title}),
              onSelected: (value) => switch (value) {
                'run' => _run(items[i]),
                'edit' => unawaited(_edit(items[i])),
                'up' => unawaited(_move(items[i], -1)),
                'down' => unawaited(_move(items[i], 1)),
                _ => unawaited(_delete(items[i])),
              },
              itemBuilder: (_) => [
                PopupMenuItem(value: 'run', child: Text(s.t('runQuick'))),
                PopupMenuItem(value: 'edit', child: Text(s.t('edit'))),
                if (i > 0)
                  PopupMenuItem(
                      value: 'up', child: Text(s.f('moveUp', {'n': i + 1}))),
                if (i < items.length - 1)
                  PopupMenuItem(
                      value: 'down',
                      child: Text(s.f('moveDown', {'n': i + 1}))),
                PopupMenuItem(value: 'delete', child: Text(s.t('delete'))),
              ],
            ),
          ),
        ),
        const SizedBox(height: AnumSpacing.xs),
      ],
    ];
  }
}

class _QuickDraft {
  const _QuickDraft(this.title, this.text, this.category);
  final String title, text;
  final QuickCategory category;
}

class _QuickDialog extends StatefulWidget {
  const _QuickDialog({
    required this.strings,
    required this.category,
    required this.categoryLabel,
    this.existing,
  });
  final CommandStrings strings;
  final QuickCommand? existing;
  final QuickCategory category;
  final String Function(QuickCategory) categoryLabel;

  @override
  State<_QuickDialog> createState() => _QuickDialogState();
}

class _QuickDialogState extends State<_QuickDialog> {
  late final _title = TextEditingController(text: widget.existing?.title);
  late final _text = TextEditingController(text: widget.existing?.text);
  late QuickCategory _category = widget.category;
  bool _invalid = false;

  @override
  void dispose() {
    _title.dispose();
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings;
    return AlertDialog(
      title: Text(widget.existing == null ? s.t('addQuick') : s.t('editQuick')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: _title,
              maxLength: 80,
              decoration: InputDecoration(labelText: s.t('quickName')),
            ),
            TextField(
              controller: _text,
              minLines: 2,
              maxLines: 6,
              maxLength: 2000,
              decoration: InputDecoration(labelText: s.t('quickText')),
            ),
            const SizedBox(height: AnumSpacing.xs),
            DropdownButtonFormField<QuickCategory>(
              initialValue: _category,
              decoration: InputDecoration(labelText: s.t('quickCategory')),
              items: [
                for (final category in QuickCategory.values)
                  DropdownMenuItem(
                      value: category,
                      child: Text(widget.categoryLabel(category))),
              ],
              onChanged: (value) =>
                  setState(() => _category = value ?? _category),
            ),
            if (_invalid)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(s.t('quickRequired'),
                    style: TextStyle(color: context.palette.stop)),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: Text(s.t('cancel'))),
        FilledButton(
          onPressed: () {
            if (_title.text.trim().isEmpty || _text.text.trim().isEmpty) {
              setState(() => _invalid = true);
              return;
            }
            Navigator.of(context)
                .pop(_QuickDraft(_title.text, _text.text, _category));
          },
          child: Text(s.t('save')),
        ),
      ],
    );
  }
}
