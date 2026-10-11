import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import 'learning_controller.dart';
import 'learning_models.dart';

/// Settings -> What ANUM learned (docs/learning.md): the learning switch, the
/// style profile, learned preferences, routines, weekly improvement notes and
/// the feedback ANUM learned from, each with edit or delete.
class LearningScreen extends StatefulWidget {
  const LearningScreen({
    required this.controller,
    this.onOpenChatMemory,
    super.key,
  });
  final LearningController controller;

  /// Leads back to "Remember things from my chats" when learning follows it.
  final VoidCallback? onOpenChatMemory;

  @override
  State<LearningScreen> createState() => _LearningScreenState();
}

class _LearningScreenState extends State<LearningScreen> {
  String? _shown;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_announce);
    widget.controller.load();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_announce);
    super.dispose();
  }

  /// Each outcome (what happened, or what failed and how to fix it) shows at
  /// the bottom of the screen, wherever the person scrolled to.
  void _announce() {
    final c = widget.controller, text = c.message;
    if (text == null || text == _shown || !mounted) {
      if (text == null) _shown = null;
      return;
    }
    _shown = text;
    final messenger = ScaffoldMessenger.of(context);
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(SnackBar(
      key: const Key('learning-message'),
      content: Text(text),
      backgroundColor:
          c.messageIsError ? Theme.of(context).colorScheme.error : null,
    ));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          return Scaffold(
            appBar: AppBar(title: const Text('What ANUM learned')),
            body: switch (c.phase) {
              LearningPhase.loading => const _Status(
                  icon: Icons.school_outlined,
                  title: 'Loading what ANUM learned',
                  loading: true,
                ),
              LearningPhase.permissionDenied => const _Status(
                  icon: Icons.lock_outline,
                  title: 'Permission required',
                  message: learningPermissionMessage,
                ),
              LearningPhase.offline => _Status(
                  icon: Icons.cloud_off_outlined,
                  title: 'You are offline',
                  message: 'Reconnect to see what ANUM learned.',
                  retry: c.load,
                ),
              LearningPhase.error => _Status(
                  icon: Icons.error_outline,
                  title: 'Could not load what ANUM learned',
                  message: c.loadError,
                  retry: c.load,
                ),
              LearningPhase.ready => _Content(
                  controller: c,
                  onOpenChatMemory: widget.onOpenChatMemory,
                ),
            },
          );
        },
      );
}

class _Content extends StatelessWidget {
  const _Content({required this.controller, this.onOpenChatMemory});
  final LearningController controller;
  final VoidCallback? onOpenChatMemory;

  @override
  Widget build(BuildContext context) {
    final c = controller, overview = c.overview!;
    final (badge, sentence) = describeState(overview.state);
    final theme = Theme.of(context);
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 760),
        child: ListView(
          padding: const EdgeInsetsDirectional.fromSTEB(
            AnumSpacing.md,
            AnumSpacing.sm,
            AnumSpacing.md,
            AnumSpacing.xl,
          ),
          children: [
            Text(
              'ANUM learns from your ratings, the drafts you edit before sending, your approval decisions and your retries, for you alone in this workspace.',
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: AnumSpacing.xs),
            Card(
              child: Column(
                children: [
                  SwitchListTile(
                    key: const Key('learning-switch'),
                    title: const Text('Learn from my activity'),
                    subtitle: Text('$badge. $sentence'),
                    value: overview.enabled,
                    onChanged:
                        c.busy || overview.state == LearningState.offOnServer
                            ? null
                            : c.setEnabled,
                  ),
                  if (overview.state == LearningState.offChatMemory &&
                      onOpenChatMemory != null)
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: TextButton.icon(
                        onPressed: onOpenChatMemory,
                        icon: const Icon(Icons.tune),
                        label: const Text('Open chat memory settings'),
                      ),
                    ),
                  Padding(
                    padding: const EdgeInsets.all(AnumSpacing.sm),
                    child: Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        _Stat('Feedback signals', overview.signals),
                        _Stat('Preferences', overview.preferences),
                        _Stat('Routines to review', overview.routinesProposed),
                        _Stat('Open suggestions', overview.suggestionsOpen),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const _Heading('Your writing style'),
            _StyleCard(controller: c, overview: overview),
            const _Heading('Learned preferences'),
            _PreferencesCard(controller: c),
            const _Heading('Weekly improvement notes'),
            _NotesCard(controller: c),
            const _Heading('Routines'),
            _RoutinesCard(controller: c),
            const _Heading('Feedback ANUM learned from'),
            _SignalsCard(controller: c),
          ],
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value);
  final String label;
  final int value;
  @override
  Widget build(BuildContext context) => Chip(label: Text('$label: $value'));
}

class _StyleCard extends StatefulWidget {
  const _StyleCard({required this.controller, required this.overview});
  final LearningController controller;
  final LearningOverview overview;
  @override
  State<_StyleCard> createState() => _StyleCardState();
}

class _StyleCardState extends State<_StyleCard> {
  late String? _tone = widget.overview.overrides.tone;
  late String? _length = widget.overview.overrides.length;
  late String? _language = widget.overview.overrides.language;
  late final _greeting = TextEditingController(
    text: widget.overview.overrides.greeting ?? '',
  );
  late final _signOff = TextEditingController(
    text: widget.overview.overrides.signOff ?? '',
  );
  late final _notes = TextEditingController(
    text: widget.overview.overrides.notes ?? '',
  );

  @override
  void dispose() {
    _greeting.dispose();
    _signOff.dispose();
    _notes.dispose();
    super.dispose();
  }

  Widget _choice(
    String label,
    String? value,
    Map<String, String> options,
    String? learned,
    ValueChanged<String?> onChanged,
  ) =>
      Padding(
        padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
        child: DropdownButtonFormField<String?>(
          initialValue: value,
          isExpanded: true,
          decoration: InputDecoration(
            labelText: label,
            helperText: learned == null
                ? 'Not learned yet'
                : 'Learned: ${options[learned] ?? learned}',
          ),
          items: [
            const DropdownMenuItem<String?>(
              value: null,
              child: Text('Use what was learned'),
            ),
            for (final entry in options.entries)
              DropdownMenuItem<String?>(
                  value: entry.key, child: Text(entry.value)),
          ],
          onChanged: onChanged,
        ),
      );

  Future<void> _confirmReset() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Reset your style?'),
        content: const Text(
          'ANUM forgets your learned style, your choices and the drafts it came from. Your preferences, routines and notes stay.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Forget my style'),
          ),
        ],
      ),
    );
    if (ok ?? false) await widget.controller.resetStyle();
  }

  @override
  Widget build(BuildContext context) {
    final learned = widget.overview.derived;
    final c = widget.controller;
    String? text(TextEditingController field) =>
        field.text.trim().isEmpty ? null : field.text.trim();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              learned.samples == 0
                  ? 'Nothing learned yet. Edit a draft before sending, or use Edit to teach on an Assistant answer.'
                  : 'Learned from ${learned.samples} draft${learned.samples == 1 ? '' : 's'} you sent or edited. Your choices below win over what was learned.',
            ),
            const SizedBox(height: AnumSpacing.sm),
            _choice(
              'Tone',
              _tone,
              toneLabels,
              learned.tone,
              (v) => setState(() => _tone = v),
            ),
            _choice(
              'Length',
              _length,
              lengthLabels,
              learned.length,
              (v) => setState(() => _length = v),
            ),
            _choice(
              'Language',
              _language,
              languageLabels,
              learned.language,
              (v) => setState(() => _language = v),
            ),
            TextField(
              controller: _greeting,
              maxLength: 80,
              decoration: InputDecoration(
                labelText: 'Greeting',
                helperText: learned.greeting == null
                    ? 'Not learned yet'
                    : 'Learned: ${learned.greeting}',
              ),
            ),
            TextField(
              controller: _signOff,
              maxLength: 80,
              decoration: InputDecoration(
                labelText: 'Sign-off',
                helperText: learned.signOff == null
                    ? 'Not learned yet'
                    : 'Learned: ${learned.signOff}',
              ),
            ),
            TextField(
              controller: _notes,
              maxLength: 500,
              minLines: 2,
              maxLines: 4,
              decoration: const InputDecoration(labelText: 'Notes for ANUM'),
            ),
            if (learned.contactLanguages.isNotEmpty)
              Text(
                'Language per contact: ${learned.contactLanguages.entries.map((e) => '${e.key}: ${languageLabels[e.value] ?? e.value}').join(', ')}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                FilledButton.icon(
                  onPressed: c.busy
                      ? null
                      : () => c.saveStyle(
                            StyleOverrides(
                              tone: _tone,
                              length: _length,
                              language: _language,
                              greeting: text(_greeting),
                              signOff: text(_signOff),
                              notes: text(_notes),
                              contactLanguages:
                                  widget.overview.overrides.contactLanguages,
                            ),
                          ),
                  icon: const Icon(Icons.check),
                  label: const Text('Save my style'),
                ),
                OutlinedButton.icon(
                  onPressed: c.busy ? null : _confirmReset,
                  icon: const Icon(Icons.restart_alt),
                  label: const Text('Reset style'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

const _scopes = {
  'all': 'Everything',
  'surface:chat': 'Assistant chat',
  'surface:email': 'Email drafts',
  'surface:whatsapp': 'WhatsApp drafts',
  'surface:task': 'Task drafts',
};

Future<(String, String)?> _editPreference(
  BuildContext context, {
  String scope = 'all',
  String text = '',
}) {
  final field = TextEditingController(text: text);
  var chosen = _scopes.containsKey(scope) ? scope : 'all';
  final custom = _scopes.containsKey(scope) ? null : scope;
  return showDialog<(String, String)>(
    context: context,
    builder: (context) => StatefulBuilder(
      builder: (context, setState) => AlertDialog(
        title: Text(text.isEmpty ? 'Add preference' : 'Edit preference'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              DropdownButtonFormField<String>(
                initialValue: chosen,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Applies to'),
                items: [
                  for (final entry in _scopes.entries)
                    DropdownMenuItem(
                      value: entry.key,
                      child: Text(entry.value),
                    ),
                ],
                onChanged: (v) => setState(() => chosen = v ?? 'all'),
              ),
              TextField(
                controller: field,
                maxLength: 300,
                autofocus: true,
                decoration: const InputDecoration(
                  labelText: 'Preference',
                  hintText: 'For example: reply to Noon customers in Arabic.',
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              if (field.text.trim().isEmpty) return;
              Navigator.pop(context, (custom ?? chosen, field.text.trim()));
            },
            child: const Text('Save'),
          ),
        ],
      ),
    ),
  );
}

class _PreferencesCard extends StatelessWidget {
  const _PreferencesCard({required this.controller});
  final LearningController controller;
  @override
  Widget build(BuildContext context) {
    final c = controller;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _SearchField(
              label: 'Search preferences',
              initial: c.preferenceQuery,
              onSearch: c.searchPreferences,
            ),
            if (c.preferences.isEmpty)
              Padding(
                padding: const EdgeInsets.all(AnumSpacing.xs),
                child: Text(
                  c.preferenceQuery.isEmpty
                      ? 'No preferences yet. Add one, or apply a suggestion from the weekly notes.'
                      : 'No preference matches "${c.preferenceQuery}".',
                ),
              ),
            for (final item in c.preferences)
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(item.text),
                subtitle: Text(
                  '${scopeLabel(item.scope)}${item.fromSuggestion ? ' · from a suggestion' : ''}',
                ),
                trailing: Wrap(
                  children: [
                    IconButton(
                      tooltip: 'Edit "${item.text}"',
                      icon: const Icon(Icons.edit_outlined),
                      onPressed: c.busy
                          ? null
                          : () async {
                              final result = await _editPreference(
                                context,
                                scope: item.scope,
                                text: item.text,
                              );
                              if (result != null) {
                                await c.updatePreference(
                                  item.id,
                                  result.$1,
                                  result.$2,
                                );
                              }
                            },
                    ),
                    IconButton(
                      tooltip: 'Delete "${item.text}"',
                      icon: const Icon(Icons.delete_outline),
                      onPressed:
                          c.busy ? null : () => c.deletePreference(item.id),
                    ),
                  ],
                ),
              ),
            Wrap(
              spacing: AnumSpacing.xs,
              children: [
                FilledButton.tonalIcon(
                  onPressed: c.busy
                      ? null
                      : () async {
                          final result = await _editPreference(context);
                          if (result != null) {
                            await c.addPreference(result.$1, result.$2);
                          }
                        },
                  icon: const Icon(Icons.add),
                  label: const Text('Add preference'),
                ),
                if (c.preferences.length < c.preferenceTotal)
                  TextButton(
                    onPressed: c.busy ? null : c.morePreferences,
                    child: Text(
                      'Show more (${c.preferences.length} of ${c.preferenceTotal})',
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _NotesCard extends StatelessWidget {
  const _NotesCard({required this.controller});
  final LearningController controller;
  @override
  Widget build(BuildContext context) {
    final c = controller;
    if (c.notes.isEmpty) {
      return const Card(
        child: ListTile(
          title: Text(
            'No notes yet. The first one is written the first week learning is on.',
          ),
        ),
      );
    }
    return Column(
      children: [
        for (final note in c.notes)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.sm),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    'Week ${note.week}',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  Wrap(
                    spacing: AnumSpacing.xs,
                    children: [
                      for (final (label, value) in note.summary)
                        Chip(label: Text('$label: $value')),
                    ],
                  ),
                  if (note.suggestions.isEmpty)
                    const Text(
                      'No suggestions this week: nothing failed or was rejected often enough to change.',
                    ),
                  for (final s in note.suggestions)
                    Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            s.title,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                          Text(
                            '${s.status == 'open' ? s.kindLabel : s.status == 'applied' ? 'Applied' : 'Dismissed'} · ${s.body}',
                          ),
                          if (s.status == 'open' && s.needsPermission != null)
                            const Text(autonomyPermissionMessage),
                          if (s.status == 'open')
                            Wrap(
                              spacing: AnumSpacing.xs,
                              children: [
                                FilledButton(
                                  onPressed: c.busy || s.needsPermission != null
                                      ? null
                                      : () => c.suggestionAction(s.id, 'apply'),
                                  child: const Text('Apply'),
                                ),
                                OutlinedButton(
                                  onPressed: c.busy
                                      ? null
                                      : () =>
                                          c.suggestionAction(s.id, 'dismiss'),
                                  child: const Text('Dismiss'),
                                ),
                              ],
                            ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

class _RoutinesCard extends StatelessWidget {
  const _RoutinesCard({required this.controller});
  final LearningController controller;
  @override
  Widget build(BuildContext context) {
    final c = controller;
    final canManage = c.overview?.canManageAutomations ?? false;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Things you do at about the same time every week or day. Create a paused automation, check it in the Workbench, then start its schedule.',
            ),
            if (!canManage) const Text(routinePermissionMessage),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton.icon(
                onPressed: c.busy || !c.learningOn ? null : c.detectRoutines,
                icon: const Icon(Icons.event_repeat),
                label: const Text('Find routines'),
              ),
            ),
            if (c.routines.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(
                  'No routines yet. After a few weeks of tasks, use Find routines.',
                ),
              ),
            for (final r in c.routines)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.sm),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      r.title,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    Text(
                      '${r.statusLabel} · seen ${r.occurrences} times · ${r.timezone} · ${r.cron}',
                    ),
                    for (final (index, step) in r.steps.indexed)
                      Text('${index + 1}. $step'),
                    Wrap(
                      spacing: AnumSpacing.xs,
                      children: [
                        if (r.status == 'proposed')
                          FilledButton(
                            onPressed: c.busy || !canManage
                                ? null
                                : () => c.routineAction(r.id, 'accept'),
                            child: const Text('Create paused automation'),
                          ),
                        if (r.status == 'accepted')
                          FilledButton(
                            onPressed: c.busy || !canManage
                                ? null
                                : () => c.routineAction(r.id, 'confirm'),
                            child: const Text('Start the schedule'),
                          ),
                        if (r.status == 'proposed')
                          OutlinedButton(
                            onPressed: c.busy
                                ? null
                                : () => c.routineAction(r.id, 'dismiss'),
                            child: const Text('Not a routine'),
                          ),
                        TextButton(
                          onPressed:
                              c.busy ? null : () => c.deleteRoutine(r.id),
                          child: const Text('Delete'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _SignalsCard extends StatelessWidget {
  const _SignalsCard({required this.controller});
  final LearningController controller;
  @override
  Widget build(BuildContext context) {
    final c = controller;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _SearchField(
              label: 'Search feedback',
              initial: c.signalQuery,
              onSearch: c.searchSignals,
            ),
            if (c.signals.isEmpty)
              Padding(
                padding: const EdgeInsets.all(AnumSpacing.xs),
                child: Text(
                  c.signalQuery.isEmpty
                      ? 'No feedback yet. Rate an Assistant answer, or edit a draft before sending.'
                      : 'Nothing matches "${c.signalQuery}".',
                ),
              ),
            for (final s in c.signals)
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(s.title),
                subtitle: Text(
                  [
                    if (s.tool != null) s.tool!,
                    if (s.contact != null) 'to ${s.contact}',
                    if (s.reason != null) 'Reason: ${s.reason}',
                    if (s.finalText != null) s.finalText!,
                  ].join(' · '),
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
                trailing: IconButton(
                  tooltip: 'Delete this feedback',
                  icon: const Icon(Icons.delete_outline),
                  onPressed: c.busy ? null : () => c.deleteSignal(s.id),
                ),
              ),
            if (c.signals.length < c.signalTotal)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton(
                  onPressed: c.busy ? null : c.moreSignals,
                  child: Text(
                    'Show more (${c.signals.length} of ${c.signalTotal})',
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _SearchField extends StatefulWidget {
  const _SearchField({
    required this.label,
    required this.initial,
    required this.onSearch,
  });
  final String label, initial;
  final Future<void> Function(String) onSearch;
  @override
  State<_SearchField> createState() => _SearchFieldState();
}

class _SearchFieldState extends State<_SearchField> {
  late final _field = TextEditingController(text: widget.initial);
  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => TextField(
        controller: _field,
        textInputAction: TextInputAction.search,
        onSubmitted: widget.onSearch,
        decoration: InputDecoration(
          labelText: widget.label,
          prefixIcon: const Icon(Icons.search),
          suffixIcon: IconButton(
            tooltip: 'Search',
            icon: const Icon(Icons.arrow_forward),
            onPressed: () => widget.onSearch(_field.text),
          ),
        ),
      );
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsetsDirectional.fromSTEB(4, 20, 4, 8),
        child: Semantics(
          header: true,
          child: Text(text, style: Theme.of(context).textTheme.titleMedium),
        ),
      );
}

class _Status extends StatelessWidget {
  const _Status({
    required this.icon,
    required this.title,
    this.message,
    this.retry,
    this.loading = false,
  });
  final IconData icon;
  final String title;
  final String? message;
  final VoidCallback? retry;
  final bool loading;
  @override
  Widget build(BuildContext context) => Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(AnumSpacing.lg),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 360),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (loading)
                  const CircularProgressIndicator()
                else
                  Icon(icon, size: 42),
                const SizedBox(height: AnumSpacing.md),
                Text(
                  title,
                  style: Theme.of(context).textTheme.titleLarge,
                  textAlign: TextAlign.center,
                ),
                if (message != null) ...[
                  const SizedBox(height: AnumSpacing.xs),
                  Text(message!, textAlign: TextAlign.center),
                ],
                if (retry != null) ...[
                  const SizedBox(height: AnumSpacing.md),
                  OutlinedButton.icon(
                    onPressed: retry,
                    icon: const Icon(Icons.refresh),
                    label: const Text('Retry'),
                  ),
                ],
              ],
            ),
          ),
        ),
      );
}
