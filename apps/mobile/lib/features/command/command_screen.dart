import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../voice/speech_service.dart';
import '../voice/voice_models.dart';
import 'command_controller.dart';
import 'command_models.dart';
import 'command_strings.dart';
import 'command_widgets.dart';
import 'quick_commands_screen.dart';

/// Tyre planning's route (`AnumRoutes.tyres`), where TyrePulse is connected.
const tyresRoute = '/tyres';

/// ANUM Command: speak or type one request, confirm the plan, follow the
/// agents live and act on the results (docs/command.md).
class CommandScreen extends StatefulWidget {
  const CommandScreen({required this.controller, super.key});
  final CommandController controller;

  @override
  State<CommandScreen> createState() => _CommandScreenState();
}

class _CommandScreenState extends State<CommandScreen> {
  CommandController get c => widget.controller;
  final _composer = TextEditingController();
  final _composerFocus = FocusNode();
  final _transcript = TextEditingController();
  bool _typing = false;

  @override
  void initState() {
    super.initState();
    c.onScreen = true;
    c.addListener(_sync);
    if (c.suggestionsPhase == LoadPhase.idle) unawaited(c.loadSuggestions());
    if (c.command != null && c.running) unawaited(c.refresh());
  }

  void _sync() {
    // The transcript field follows recognition while the microphone is on.
    if (c.listeningActive && _transcript.text != c.transcript) {
      _transcript.text = c.transcript;
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    c.onScreen = false;
    c.removeListener(_sync);
    _composer.dispose();
    _composerFocus.dispose();
    _transcript.dispose();
    super.dispose();
  }

  CommandStrings get s => CommandStrings.of(context);

  void _snack(String text) => ScaffoldMessenger.maybeOf(context)
      ?.showSnackBar(SnackBar(content: Text(text)));

  String _failureText(CommandFailure failure) => switch (failure.kind) {
        FailureKind.permission => s.t('permissionPlan'),
        FailureKind.offline => s.t('offline'),
        FailureKind.notFound => s.t('notFound'),
        FailureKind.other => failure.message,
      };

  Future<void> _submitComposer({String source = 'typed'}) async {
    final text = _composer.text.trim();
    if (text.isEmpty) return;
    _composer.clear();
    setState(() => _typing = false);
    await c.startFrom(text, source: source);
  }

  void _openQuickCommands() =>
      Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => QuickCommandsScreen(controller: c)));

  Future<void> _openHistory() async {
    unawaited(c.loadHistory());
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (sheetContext) => _HistorySheet(
          controller: c,
          onOpen: (id) {
            Navigator.of(sheetContext).pop();
            unawaited(c.open(id));
          }),
    );
  }

  @override
  Widget build(BuildContext context) {
    final strings = s;
    return PopScope(
      canPop: c.stage == CommandStage.start,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) c.back();
      },
      child: Scaffold(
        body: AnumBackdrop(
          child: SafeArea(
            child: Column(
              children: [
                _TopBar(
                  onBack: () {
                    if (!c.back()) Navigator.of(context).maybePop();
                  },
                  onHistory: _openHistory,
                  onQuick: _openQuickCommands,
                  strings: strings,
                ),
                Expanded(child: _stage(strings)),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _stage(CommandStrings strings) => switch (c.stage) {
        CommandStage.start => _start(strings),
        CommandStage.listening => _listening(strings),
        CommandStage.understanding => _understanding(strings),
        CommandStage.plan => _plan(strings),
        CommandStage.executing => _executing(strings),
        CommandStage.results => _results(strings),
      };

  // -- start ------------------------------------------------------------------------

  Widget _start(CommandStrings s) {
    final p = context.palette;
    final running = c.command != null && (c.running || c.notStarted.isNotEmpty);
    return _Body(children: [
      CommandTitle(title: s.t('title'), subtitle: s.t('tagline')),
      if (running) _runningCard(s),
      CommandOrb(state: VoiceState.idle, level: c.level),
      Text(s.t('ready'),
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.titleMedium),
      _suggestions(s),
      if (c.attachments.isNotEmpty || c.attaching || c.attachError != null)
        _attachments(s),
      if (_typing ||
          MediaQuery.sizeOf(context).width >= AnumBreakpoints.expanded)
        _composerField(s, autofocus: _typing),
      Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          IconButton.outlined(
            tooltip: s.t('typeInstead'),
            onPressed: () {
              setState(() => _typing = true);
              _composerFocus.requestFocus();
            },
            icon: const Icon(Icons.keyboard_outlined),
          ),
          _MicButton(label: s.t('tapToSpeak'), onPressed: c.startListening),
          IconButton.outlined(
            tooltip: s.t('attach'),
            onPressed: c.attaching ? null : c.attachFile,
            icon: const Icon(Icons.photo_camera_outlined),
          ),
        ],
      ),
      Text(s.t('tapToSpeak'),
          textAlign: TextAlign.center,
          style:
              Theme.of(context).textTheme.bodySmall?.copyWith(color: p.muted)),
    ]);
  }

  Widget _runningCard(CommandStrings s) {
    final view = c.command!;
    return AnumSurface(
      onTap: () => c.goTo(
          view.status.finished || view.status == CommandStatus.waitingApproval
              ? CommandStage.results
              : CommandStage.executing),
      semanticLabel: '${s.t('running')}: ${view.text}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            const AnumIconChip(icon: Icons.autorenew, tone: AnumTone.info),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(s.t('running'),
                      style: Theme.of(context).textTheme.labelLarge),
                  Text(view.text, maxLines: 2, overflow: TextOverflow.ellipsis),
                ],
              ),
            ),
            Text(s.t('openRunning')),
          ]),
          const SizedBox(height: AnumSpacing.xs),
          CommandProgress(value: view.progress, label: view.text),
        ],
      ),
    );
  }

  Widget _suggestions(CommandStrings s) {
    switch (c.suggestionsPhase) {
      case LoadPhase.idle:
      case LoadPhase.loading:
        return Text(s.t('suggestionsLoading'), textAlign: TextAlign.center);
      case LoadPhase.error:
        return Column(children: [
          Text(s.t('suggestionsError'), textAlign: TextAlign.center),
          TextButton.icon(
              onPressed: c.loadSuggestions,
              icon: const Icon(Icons.refresh),
              label: Text(s.t('retry'))),
        ]);
      case LoadPhase.ready:
        return Wrap(
          alignment: WrapAlignment.center,
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [
            for (final item in c.suggestions)
              ActionChip(
                avatar: Icon(
                    switch (item.kind) {
                      'quick' => Icons.bolt_outlined,
                      'recent' => Icons.history,
                      _ => Icons.auto_awesome_outlined,
                    },
                    size: 18),
                label: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 260),
                  child: Text(item.title,
                      maxLines: 2, overflow: TextOverflow.ellipsis),
                ),
                tooltip: item.text,
                onPressed: () => c.startFrom(item.text,
                    source:
                        item.kind == 'quick' ? 'quick_command' : 'suggestion',
                    quickCommandId: item.quickCommandId),
              ),
          ],
        );
    }
  }

  Widget _attachments(CommandStrings s) => Wrap(
        alignment: WrapAlignment.center,
        spacing: AnumSpacing.xs,
        runSpacing: AnumSpacing.xs,
        children: [
          for (final item in c.attachments)
            InputChip(
              avatar: const Icon(Icons.attach_file, size: 18),
              label: Text(item.name, overflow: TextOverflow.ellipsis),
              deleteButtonTooltipMessage:
                  s.f('removeAttachment', {'name': item.name}),
              onDeleted: () => c.removeAttachment(item),
            ),
          if (c.attaching) Chip(label: Text(s.t('attaching'))),
          if (c.attachError != null)
            Text(_failureText(c.attachError!),
                style: TextStyle(color: context.palette.stop)),
        ],
      );

  Widget _composerField(CommandStrings s, {bool autofocus = false}) =>
      TextField(
        controller: _composer,
        focusNode: _composerFocus,
        autofocus: autofocus,
        minLines: 1,
        maxLines: 4,
        maxLength: 4000,
        textInputAction: TextInputAction.send,
        onSubmitted: (_) => _submitComposer(),
        decoration: InputDecoration(
          hintText: s.t('askHint'),
          counterText: '',
          prefixIcon: const Icon(Icons.search),
          suffixIcon: IconButton(
            tooltip: s.t('send'),
            onPressed: _submitComposer,
            icon: const Icon(Icons.arrow_forward),
          ),
        ),
      );

  // -- listening ------------------------------------------------------------------

  Widget _listening(CommandStrings s) {
    final mic = c.micState;
    final theme = Theme.of(context);
    return _Body(children: [
      CommandTitle(title: s.t('listening'), subtitle: s.t('speakNow')),
      Center(
        child: SegmentedButton<String>(
          expandedInsets: EdgeInsets.zero,
          segments: [
            ButtonSegment(
                value: 'en',
                label: Text(s.t('english'),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    textAlign: TextAlign.center)),
            ButtonSegment(
                value: 'ar',
                label: Text(s.t('arabic'),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    textAlign: TextAlign.center)),
          ],
          selected: {c.language},
          onSelectionChanged:
              c.listeningActive ? null : (value) => c.setLanguage(value.first),
        ),
      ),
      CommandOrb(
          state: c.listeningActive ? VoiceState.listening : VoiceState.idle,
          level: c.level),
      Semantics(
        liveRegion: true,
        child: Text(durationLabel(c.elapsed),
            textAlign: TextAlign.center, style: theme.textTheme.titleLarge),
      ),
      if (mic == SpeechAvailability.denied ||
          mic == SpeechAvailability.unavailable)
        AnumEmpty(
          icon: Icons.mic_off_outlined,
          title: s.t('typeInstead'),
          message: mic == SpeechAvailability.denied
              ? s.t('micDenied')
              : s.t('micUnavailable'),
          action: mic == SpeechAvailability.denied
              ? s.t('openSettings')
              : s.t('typeInstead'),
          onAction: mic == SpeechAvailability.denied
              ? c.openMicSettings
              : () {
                  c.goTo(CommandStage.start);
                  setState(() => _typing = true);
                },
        ),
      if (c.heardNothing && !c.listeningActive)
        Text(s.t('heardNothing'), textAlign: TextAlign.center),
      AnumSurface(
        child: TextField(
          controller: _transcript,
          readOnly: c.listeningActive,
          minLines: 3,
          maxLines: 8,
          maxLength: 4000,
          decoration: InputDecoration(
            labelText: s.t('transcript'),
            helperText: s.t('transcriptHint'),
            helperMaxLines: 3,
            border: InputBorder.none,
          ),
        ),
      ),
      if (c.listeningActive)
        Center(
          child: Semantics(
            button: true,
            label: s.t('stop'),
            excludeSemantics: true,
            child: FilledButton(
              style: FilledButton.styleFrom(
                  backgroundColor: context.palette.stop,
                  shape: const CircleBorder(),
                  minimumSize: const Size(72, 72)),
              onPressed: c.stopListening,
              child: const Icon(Icons.stop, size: 32),
            ),
          ),
        )
      else
        Wrap(
          alignment: WrapAlignment.center,
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.sm,
          children: [
            if (mic == SpeechAvailability.ready)
              OutlinedButton.icon(
                onPressed: c.startListening,
                icon: const Icon(Icons.mic_none),
                label: Text(s.t('tapToSpeak')),
              ),
            SizedBox(
              width: 220,
              child: CommandGradientButton(
                label: s.t('useTranscript'),
                icon: Icons.arrow_forward,
                onPressed: () => c.submit(_transcript.text, source: 'voice'),
              ),
            ),
          ],
        ),
      Center(
        child: TextButton(
            onPressed: c.cancelListening, child: Text(s.t('cancel'))),
      ),
    ]);
  }

  // -- understanding ----------------------------------------------------------------

  Future<void> _editRequest() async {
    final strings = s;
    final edited = await showDialog<String>(
      context: context,
      builder: (dialogContext) => _TextEditDialog(
          title: strings.t('edit'),
          initial: c.requestText,
          saveLabel: strings.t('replan'),
          cancelLabel: strings.t('cancel')),
    );
    if (edited != null) await c.editRequest(edited);
  }

  Widget _requestCard(CommandStrings s, {required String label}) {
    final theme = Theme.of(context);
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Expanded(
              child: Semantics(
                header: true,
                child: Text(label, style: theme.textTheme.titleMedium),
              ),
            ),
            TextButton.icon(
              onPressed: c.planning ? null : _editRequest,
              icon: const Icon(Icons.edit_outlined, size: 18),
              label: Text(s.t('edit')),
            ),
          ]),
          const SizedBox(height: AnumSpacing.xs),
          Text('“${c.requestText}”', style: theme.textTheme.bodyLarge),
        ],
      ),
    );
  }

  Widget _understanding(CommandStrings s) => _Body(children: [
        CommandTitle(
            title: s.t('understanding'), subtitle: s.t('convertingRequest')),
        _requestCard(s, label: s.t('understood')),
        if (c.planning)
          AnumSurface(
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2)),
                const SizedBox(width: AnumSpacing.sm),
                Flexible(child: Text(s.t('analyzing'))),
              ],
            ),
          ),
        if (c.planError != null)
          AnumEmpty(
            icon: c.planError!.kind == FailureKind.permission
                ? Icons.lock_outline
                : Icons.error_outline,
            title: s.t('planTitle'),
            message: c.planError!.kind == FailureKind.other
                ? s.f('planError', {'error': c.planError!.message})
                : _failureText(c.planError!),
            action: c.planError!.kind == FailureKind.permission
                ? null
                : s.t('retry'),
            onAction: c.planError!.kind == FailureKind.permission
                ? null
                : c.retryPlan,
          ),
      ]);

  // -- plan -------------------------------------------------------------------------

  Future<void> _editStep(int index) async {
    final strings = s;
    final edited = await showDialog<String>(
      context: context,
      builder: (_) => _TextEditDialog(
          title: strings.f('editStep', {'n': index + 1}),
          initial: c.steps[index].instruction,
          saveLabel: strings.t('saveStep'),
          cancelLabel: strings.t('cancel'),
          maxLength: 1000),
    );
    if (edited != null) c.editStep(index, edited);
  }

  Widget _plan(CommandStrings s) {
    final agents = <String, CommandAgent>{
      for (final step in c.steps) step.agent.id: step.agent
    }.values.toList();
    final steps = <Widget>[
      if (c.steps.isEmpty)
        AnumEmpty(
            icon: Icons.playlist_remove,
            title: s.t('planTitle'),
            message: s.t('noSteps'),
            action: s.t('edit'),
            onAction: _editRequest),
      for (var i = 0; i < c.steps.length; i++) _planStep(s, i),
    ];
    final footer = <Widget>[
      if (c.approvalsAhead > 0)
        AnumPill(
            label: s.f('approvalsAhead', {'count': c.approvalsAhead}),
            tone: AnumTone.warn,
            icon: Icons.pending_actions),
      if (c.hasBlockedStep)
        AnumPill(
            label: s.t('blockedAhead'), tone: AnumTone.stop, icon: Icons.block),
      if (c.runError != null)
        Text(_failureText(c.runError!),
            style: TextStyle(color: context.palette.stop)),
      CommandGradientButton(
        label: s.t('startExecution'),
        caption: s.f('startHint', {'count': c.steps.length}),
        icon: Icons.play_arrow_rounded,
        busy: c.starting,
        onPressed: c.canStart ? c.start : null,
      ),
    ];
    final side = <Widget>[
      _requestCard(s, label: s.t('understood')),
      if (agents.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Semantics(
                  header: true,
                  child: Text(s.t('anumWillUse'),
                      style: Theme.of(context).textTheme.titleMedium)),
              const SizedBox(height: AnumSpacing.sm),
              Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.sm,
                children: [for (final agent in agents) AgentChip(agent: agent)],
              ),
            ],
          ),
        ),
    ];
    return _Body(
      wide: true,
      header:
          CommandTitle(title: s.t('planTitle'), subtitle: s.t('planSubtitle')),
      columns: (main: [...steps, ...footer], side: side),
      children: [...side, ...steps, ...footer],
    );
  }

  Widget _planStep(CommandStrings s, int index) {
    final step = c.steps[index];
    final theme = Theme.of(context);
    final n = index + 1;
    return AnumSurface(
      semanticLabel: null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AnumIconChip(
                  icon: agentIcon(step.agent.id),
                  tone: agentTone(step.agent.id)),
              const SizedBox(width: AnumSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${s.f('step', {'n': n})} · ${step.agent.name}',
                        style: theme.textTheme.labelLarge),
                    Text(step.instruction, style: theme.textTheme.bodyMedium),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              switch (step.approval) {
                ApprovalNeed.required => AnumPill(
                    label: s.t('needsApproval'),
                    tone: AnumTone.warn,
                    icon: Icons.pending_actions),
                ApprovalNeed.blocked => AnumPill(
                    label: s.t('blocked'),
                    tone: AnumTone.stop,
                    icon: Icons.block),
                ApprovalNeed.none => AnumPill(
                    label: s.t('noApproval'),
                    tone: AnumTone.ok,
                    icon: Icons.check_circle_outline),
              },
              AnumPill(label: riskLabel(s, step.riskLevel)),
              if (!step.connected)
                AnumPill(
                    label: s.t('notConnected'),
                    tone: AnumTone.warn,
                    icon: Icons.link_off),
            ],
          ),
          if (step.note != null) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(step.note!,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: context.palette.muted)),
          ],
          // A tyre step with the tyre tools but no saved connection: connect it
          // under Tyre planning (docs/tyre-planning.md), then plan again.
          if (!step.connected &&
              step.agent.id == 'tyre' &&
              step.tool != null &&
              step.tool != 'anum.respond')
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                onPressed: () => Navigator.of(context).pushNamed(tyresRoute),
                icon: const Icon(Icons.link),
                label: Text(s.t('connectTyre')),
              ),
            ),
          if (step.approval == ApprovalNeed.blocked) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(step.approvalReason,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: context.palette.stop)),
          ],
          Wrap(
            alignment: WrapAlignment.end,
            children: [
              IconButton(
                  tooltip: s.f('editStep', {'n': n}),
                  onPressed: () => _editStep(index),
                  icon: const Icon(Icons.edit_outlined)),
              IconButton(
                  tooltip: s.f('moveUp', {'n': n}),
                  onPressed: index == 0 ? null : () => c.moveStep(index, -1),
                  icon: const Icon(Icons.arrow_upward)),
              IconButton(
                  tooltip: s.f('moveDown', {'n': n}),
                  onPressed: index == c.steps.length - 1
                      ? null
                      : () => c.moveStep(index, 1),
                  icon: const Icon(Icons.arrow_downward)),
              IconButton(
                  tooltip: s.f('removeStep', {'n': n}),
                  onPressed: () => c.removeStep(index),
                  icon: const Icon(Icons.delete_outline)),
            ],
          ),
        ],
      ),
    );
  }

  // -- executing ----------------------------------------------------------------------

  Future<void> _confirmStop() async {
    final strings = s;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        content: Text(strings.t('stopConfirm')),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(strings.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: Text(strings.t('stopAll'))),
        ],
      ),
    );
    if (confirmed == true) await c.stop();
  }

  Widget _executing(CommandStrings s) {
    final view = c.command;
    if (view == null) {
      return _Body(children: [
        CommandTitle(title: s.t('executing')),
        if (c.loadError != null)
          AnumEmpty(
              icon: Icons.error_outline,
              title: s.t('executing'),
              message: c.loadError!.kind == FailureKind.other
                  ? s.f('loadError', {'error': c.loadError!.message})
                  : _failureText(c.loadError!),
              action: s.t('newCommand'),
              onAction: c.newCommand)
        else
          const Center(child: CircularProgressIndicator()),
      ]);
    }
    final agents = <Widget>[
      for (final step in view.steps) _agentCard(s, step),
      AnumPill(
        label: s.f('agentsParallel', {'count': view.steps.length}),
        tone: AnumTone.info,
        icon: Icons.hub_outlined,
      ),
      if (c.notStarted.isNotEmpty)
        OutlinedButton.icon(
            onPressed: c.startRemaining,
            icon: const Icon(Icons.play_arrow),
            label: Text(s.t('startRemaining'))),
    ];
    final actions = <Widget>[
      if (c.runError != null)
        Text(_failureText(c.runError!),
            style: TextStyle(color: context.palette.stop)),
      CommandGradientButton(
        label: s.t('keepWorking'),
        icon: Icons.open_in_new,
        onPressed: () => Navigator.of(context).maybePop(),
      ),
      OutlinedButton.icon(
        onPressed: c.stopping ? null : _confirmStop,
        icon: const Icon(Icons.stop_circle_outlined),
        label: Text(s.t('stopAll')),
      ),
    ];
    final activity = [_timeline(s, view, limit: 12)];
    return _Body(
      wide: true,
      header:
          CommandTitle(title: s.t('executing'), subtitle: s.t('agentsWorking')),
      columns: (main: [...agents, ...actions], side: activity),
      children: [...agents, ...activity, ...actions],
    );
  }

  Widget _agentCard(CommandStrings s, CommandStepView step) {
    final theme = Theme.of(context);
    final error = c.stepErrors[step.taskId];
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              AnumIconChip(
                  icon: agentIcon(step.agent.id),
                  tone: agentTone(step.agent.id)),
              const SizedBox(width: AnumSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(step.agent.name, style: theme.textTheme.titleSmall),
                    Text(step.title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: context.palette.muted)),
                  ],
                ),
              ),
              const SizedBox(width: AnumSpacing.xs),
              Icon(stepStatusIcon(step.status),
                  color: anumToneColor(context, stepStatusTone(step.status))),
            ],
          ),
          const SizedBox(height: AnumSpacing.xs),
          Row(children: [
            Expanded(
                child: CommandProgress(
                    value: step.progress,
                    label: '${step.agent.name}: ${step.title}')),
            const SizedBox(width: AnumSpacing.xs),
            Text(stepStatusLabel(s, step.status),
                style: theme.textTheme.labelSmall),
          ]),
          if (error != null) ...[
            const SizedBox(height: AnumSpacing.xxs),
            Text(error, style: TextStyle(color: context.palette.stop)),
          ],
        ],
      ),
    );
  }

  Widget _timeline(CommandStrings s, CommandView view, {int? limit}) {
    final theme = Theme.of(context);
    final entries = limit == null || view.timeline.length <= limit
        ? view.timeline
        : view.timeline.sublist(view.timeline.length - limit);
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Expanded(
              child: Semantics(
                header: true,
                child: Text(s.t('liveActivity'),
                    style: theme.textTheme.titleMedium),
              ),
            ),
            if (view.status.active)
              AnumPill(
                label: c.live ? s.t('liveConnected') : s.t('livePolling'),
                tone: c.live ? AnumTone.ok : AnumTone.neutral,
                icon: c.live ? Icons.sensors : Icons.sync,
              ),
          ]),
          const SizedBox(height: AnumSpacing.xs),
          if (entries.isEmpty)
            Text(s.t('noActivity'),
                style: TextStyle(color: context.palette.muted))
          else
            for (final entry in entries)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 48,
                      child: Text(clockTime(entry.at),
                          style: theme.textTheme.labelSmall),
                    ),
                    Icon(agentIcon(entry.agent.id),
                        size: 18,
                        color:
                            anumToneColor(context, agentTone(entry.agent.id))),
                    const SizedBox(width: AnumSpacing.xs),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                              '${entry.agent.name}: ${eventLabel(s, entry.type)}',
                              style: theme.textTheme.bodyMedium),
                          Text(entry.title,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.bodySmall
                                  ?.copyWith(color: context.palette.muted)),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }

  // -- results ----------------------------------------------------------------------

  int _tab = 0;

  Future<void> _approveAll(CommandView view) async {
    final strings = s;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title:
            Text(strings.f('approveAllTitle', {'count': view.pending.length})),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(strings.t('approveAllBody')),
              for (final approval in view.pending) ...[
                const SizedBox(height: AnumSpacing.sm),
                Text(approval.action,
                    style: Theme.of(dialogContext).textTheme.titleSmall),
                Text(approval.reason),
                Text(
                    strings.f('payloadHash',
                        {'hash': shortHash(approval.payloadHash)}),
                    style: Theme.of(dialogContext).textTheme.bodySmall),
              ],
            ],
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text(strings.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: Text(strings.t('approve'))),
        ],
      ),
    );
    if (confirmed != true) return;
    final outcome = await c.approveAll();
    if (!mounted) return;
    _snack([
      strings.f('approved', {'count': outcome.approved}),
      ...outcome.failures,
    ].join('\n'));
  }

  Future<void> _share(CommandView view) async {
    final strings = s;
    final choice = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.save_alt),
              title: Text(strings.t('shareSave')),
              onTap: () => Navigator.of(sheetContext).pop('save'),
            ),
            ListTile(
              leading: const Icon(Icons.copy),
              title: Text(strings.t('shareCopy')),
              onTap: () => Navigator.of(sheetContext).pop('copy'),
            ),
          ],
        ),
      ),
    );
    if (choice == 'copy') {
      await Clipboard.setData(ClipboardData(text: view.plainSummary()));
      if (mounted) _snack(strings.t('copied'));
    } else if (choice == 'save') {
      try {
        if (await c.saveReport() && mounted) _snack(strings.t('saved'));
      } on Object catch (error) {
        if (mounted) _snack(_failureText(CommandFailure.of(error)));
      }
    }
  }

  Future<void> _saveFile(CommandFile file) async {
    final strings = s;
    try {
      if (await c.saveFile(file) && mounted) _snack(strings.t('saved'));
    } on Object catch (error) {
      if (mounted) _snack(_failureText(CommandFailure.of(error)));
    }
  }

  void _openReport() => Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => _ReportPage(controller: c)));

  Future<void> _onNextStep(NextStep step, CommandView view) async {
    final strings = s;
    switch (step.kind) {
      case 'review_approvals':
        await _approveAll(view);
      case 'save_quick_command':
        final failure = await c.saveAsQuick(step.text ?? view.text);
        if (!mounted) return;
        _snack(
            failure == null ? strings.t('savedQuick') : _failureText(failure));
        if (failure == null) unawaited(c.loadNextSteps());
      default:
        final text = step.text;
        if (text != null) await c.startFrom(text, source: 'next_step');
    }
  }

  Widget _results(CommandStrings s) {
    final view = c.command;
    if (view == null) return _executing(s);
    final theme = Theme.of(context);
    final duration = view.durationSeconds;
    final (title, subtitle) = switch (view.status) {
      CommandStatus.completed => (
          s.t('resultsReady'),
          duration == null
              ? s.t('resultsSubtitle')
              : s.f('completedIn', {
                  'duration': durationLabel(Duration(seconds: duration.round()))
                })
        ),
      CommandStatus.partial => (s.t('resultsReady'), s.t('partialResults')),
      CommandStatus.failed => (s.t('resultsReady'), s.t('failedResults')),
      CommandStatus.cancelled => (s.t('stopped'), s.t('cancelledResults')),
      CommandStatus.waitingApproval => (
          s.t('resultsReady'),
          s.t('waitingResults')
        ),
      _ => (s.t('executing'), s.t('agentsWorking')),
    };
    final tabs = Center(
      child: SegmentedButton<int>(
        expandedInsets: EdgeInsets.zero,
        segments: [
          ButtonSegment(
              value: 0,
              label: Text(s.t('summary'),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.center)),
          ButtonSegment(
              value: 1,
              label: Text(s.t('details'),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.center)),
          ButtonSegment(
              value: 2,
              label: Text(s.t('files'),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.center)),
        ],
        selected: {_tab},
        showSelectedIcon: false,
        onSelectionChanged: (value) => setState(() => _tab = value.first),
      ),
    );
    final content = switch (_tab) {
      0 => [
          for (final step in view.steps)
            AnumSurface(
              padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.sm),
              child: AnumRow(
                icon: agentIcon(step.agent.id),
                tone: agentTone(step.agent.id),
                title: step.title,
                subtitle: _firstLine(step.result),
                // Status in words (never colour alone), the icon repeats it.
                detail: stepStatusLabel(s, step.status),
                trailing: ExcludeSemantics(
                  child: Icon(stepStatusIcon(step.status),
                      color:
                          anumToneColor(context, stepStatusTone(step.status))),
                ),
                onTap: () => setState(() => _tab = 1),
              ),
            ),
        ],
      1 => [
          for (final step in view.steps) _detailCard(s, step),
          _timeline(s, view),
        ],
      _ => [
          if (view.files.isEmpty)
            AnumEmpty(
                icon: Icons.folder_open_outlined,
                title: s.t('files'),
                message: s.t('noFiles'))
          else
            for (final file in view.files)
              AnumSurface(
                padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.sm),
                child: AnumRow(
                  icon: file.kind == 'attachment'
                      ? Icons.attach_file
                      : Icons.description_outlined,
                  title: file.name,
                  subtitle: file.contentType,
                  trailing: IconButton(
                    tooltip: s.f('saveFile', {'name': file.name}),
                    onPressed: () => _saveFile(file),
                    icon: const Icon(Icons.save_alt),
                  ),
                ),
              ),
        ],
    };
    final approvals = <Widget>[
      if (view.pending.isNotEmpty)
        AnumSurface(
          accent: context.palette.warn,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(s.f('pendingCount', {'count': view.pending.length}),
                  style: theme.textTheme.titleMedium),
              for (final approval in view.pending)
                Text('• ${approval.action}: ${approval.reason}',
                    maxLines: 3, overflow: TextOverflow.ellipsis),
              const SizedBox(height: AnumSpacing.sm),
              CommandGradientButton(
                label: s.t('approveAll'),
                icon: Icons.verified_outlined,
                busy: c.approving,
                onPressed: () => _approveAll(view),
              ),
            ],
          ),
        ),
    ];
    final actions = Wrap(
      alignment: WrapAlignment.center,
      spacing: AnumSpacing.xs,
      runSpacing: AnumSpacing.xs,
      children: [
        OutlinedButton.icon(
            onPressed: () => c.startFrom(view.text, source: 'typed'),
            icon: const Icon(Icons.edit_outlined),
            label: Text(s.t('edit'))),
        OutlinedButton.icon(
            onPressed: () => _share(view),
            icon: const Icon(Icons.ios_share),
            label: Text(s.t('share'))),
        OutlinedButton.icon(
            onPressed: _openReport,
            icon: const Icon(Icons.article_outlined),
            label: Text(s.t('viewFullReport'))),
        FilledButton.icon(
            onPressed: () {
              c.newCommand();
              setState(() => _typing = true);
            },
            icon: const Icon(Icons.mic_none),
            label: Text(s.t('askFollowUp'))),
      ],
    );
    final next = <Widget>[
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
                header: true,
                child:
                    Text(s.t('nextSteps'), style: theme.textTheme.titleMedium)),
            Text(s.t('nextStepsSubtitle'),
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: context.palette.muted)),
            const SizedBox(height: AnumSpacing.xs),
            if (c.nextPhase == LoadPhase.loading)
              const LinearProgressIndicator()
            else if (c.nextPhase == LoadPhase.error)
              TextButton.icon(
                  onPressed: c.loadNextSteps,
                  icon: const Icon(Icons.refresh),
                  label: Text(s.t('retry')))
            else if (c.nextSteps.isEmpty)
              Text(s.t('noNextSteps'))
            else
              for (final step in c.nextSteps)
                AnumRow(
                  icon: switch (step.kind) {
                    'review_approvals' => Icons.verified_outlined,
                    'save_quick_command' => Icons.bookmark_add_outlined,
                    _ => Icons.arrow_forward,
                  },
                  title: step.label,
                  onTap: () => _onNextStep(step, view),
                ),
          ],
        ),
      ),
      _composerField(s),
    ];
    return _Body(
      wide: true,
      header: CommandTitle(title: title, subtitle: subtitle),
      columns: (
        main: [tabs, ...content],
        side: [...approvals, actions, ...next]
      ),
      children: [...approvals, tabs, ...content, actions, ...next],
    );
  }

  Widget _detailCard(CommandStrings s, CommandStepView step) {
    final theme = Theme.of(context);
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            AnumIconChip(
                icon: agentIcon(step.agent.id), tone: agentTone(step.agent.id)),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(
              child: Text('${step.position}. ${step.title}',
                  style: theme.textTheme.titleSmall),
            ),
          ]),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            AnumPill(label: step.agent.name),
            AnumPill(
                label: stepStatusLabel(s, step.status),
                tone: stepStatusTone(step.status)),
            if (step.tool != null) AnumPill(label: step.tool!),
            AnumPill(label: riskLabel(s, step.riskLevel)),
          ]),
          const SizedBox(height: AnumSpacing.xs),
          SelectableText(step.result?.trim().isNotEmpty == true
              ? step.result!.trim()
              : s.t('noResult')),
          for (final approval in step.approvals) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text('${approval.action}: ${approval.status}',
                style: theme.textTheme.labelLarge),
            Text(s.f('payloadHash', {'hash': shortHash(approval.payloadHash)}),
                style: theme.textTheme.bodySmall),
          ],
        ],
      ),
    );
  }

  static String? _firstLine(String? value) {
    final text = value?.trim();
    if (text == null || text.isEmpty) return null;
    final line = text.split('\n').first;
    return line.length > 140 ? '${line.substring(0, 139)}…' : line;
  }
}

/// Scrolling page body: one column on phones, and with [columns] two side by
/// side from the expanded breakpoint.
class _Body extends StatelessWidget {
  const _Body({
    required this.children,
    this.header,
    this.columns,
    this.wide = false,
  });

  final List<Widget> children;
  final Widget? header;
  final ({List<Widget> main, List<Widget> side})? columns;
  final bool wide;

  static List<Widget> _spaced(List<Widget> items) => [
        for (var i = 0; i < items.length; i++) ...[
          items[i],
          if (i < items.length - 1) const SizedBox(height: AnumSpacing.md),
        ]
      ];

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, box) {
        final gutter = box.maxWidth < AnumBreakpoints.compact
            ? AnumSpacing.md
            : AnumSpacing.lg;
        final split =
            columns != null && box.maxWidth >= AnumBreakpoints.expanded;
        final Widget content;
        if (split) {
          content = Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (header != null) ...[
                header!,
                const SizedBox(height: AnumSpacing.lg)
              ],
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                      flex: 3,
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: _spaced(columns!.main))),
                  const SizedBox(width: AnumSpacing.lg),
                  Expanded(
                      flex: 2,
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: _spaced(columns!.side))),
                ],
              ),
            ],
          );
        } else {
          content = Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: _spaced([if (header != null) header!, ...children]),
          );
        }
        return ListView(
          padding: EdgeInsets.fromLTRB(
              gutter, AnumSpacing.xs, gutter, AnumSpacing.xl),
          children: [
            Center(
              child: ConstrainedBox(
                constraints: BoxConstraints(
                    maxWidth: split ||
                            wide && box.maxWidth >= AnumBreakpoints.expanded
                        ? 1100
                        : 720),
                child: content,
              ),
            ),
          ],
        );
      });
}

class _TopBar extends StatelessWidget {
  const _TopBar({
    required this.onBack,
    required this.onHistory,
    required this.onQuick,
    required this.strings,
  });
  final VoidCallback onBack, onHistory, onQuick;
  final CommandStrings strings;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.xs),
        child: Row(children: [
          IconButton(
            tooltip: strings.t('back'),
            onPressed: onBack,
            icon: const BackButtonIcon(),
          ),
          const Spacer(),
          IconButton(
            tooltip: strings.t('history'),
            onPressed: onHistory,
            icon: const Icon(Icons.history),
          ),
          IconButton(
            tooltip: strings.t('quickCommands'),
            onPressed: onQuick,
            icon: const Icon(Icons.bolt_outlined),
          ),
        ]),
      );
}

class _MicButton extends StatelessWidget {
  const _MicButton({required this.label, required this.onPressed});
  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      button: true,
      label: label,
      excludeSemantics: true,
      child: DecoratedBox(
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: LinearGradient(colors: [p.sky, p.violet]),
          boxShadow: [
            BoxShadow(color: p.violet.withValues(alpha: 0.45), blurRadius: 28)
          ],
        ),
        child: Material(
          type: MaterialType.transparency,
          shape: const CircleBorder(),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: onPressed,
            child: SizedBox.square(
              dimension: 76,
              child: Icon(Icons.mic,
                  size: 34,
                  color: Theme.of(context).brightness == Brightness.dark
                      ? p.background
                      : Colors.white),
            ),
          ),
        ),
      ),
    );
  }
}

class _TextEditDialog extends StatefulWidget {
  const _TextEditDialog({
    required this.title,
    required this.initial,
    required this.saveLabel,
    required this.cancelLabel,
    this.maxLength = 4000,
  });
  final String title, initial, saveLabel, cancelLabel;
  final int maxLength;

  @override
  State<_TextEditDialog> createState() => _TextEditDialogState();
}

class _TextEditDialogState extends State<_TextEditDialog> {
  late final _text = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(widget.title),
        content: TextField(
          controller: _text,
          autofocus: true,
          minLines: 2,
          maxLines: 8,
          maxLength: widget.maxLength,
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: Text(widget.cancelLabel)),
          FilledButton(
              onPressed: () {
                final value = _text.text.trim();
                if (value.isNotEmpty) Navigator.of(context).pop(value);
              },
              child: Text(widget.saveLabel)),
        ],
      );
}

class _HistorySheet extends StatelessWidget {
  const _HistorySheet({required this.controller, required this.onOpen});
  final CommandController controller;
  final ValueChanged<String> onOpen;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final s = CommandStrings.of(context);
          final c = controller;
          final items = c.history;
          return DraggableScrollableSheet(
            expand: false,
            initialChildSize: 0.7,
            minChildSize: 0.4,
            maxChildSize: 0.95,
            builder: (context, scroll) => ListView(
              controller: scroll,
              padding: const EdgeInsets.all(AnumSpacing.md),
              children: [
                Semantics(
                    header: true,
                    child: Text(s.t('history'),
                        style: Theme.of(context).textTheme.titleLarge)),
                const SizedBox(height: AnumSpacing.sm),
                if (c.historyPhase == LoadPhase.error && items.isEmpty)
                  AnumEmpty(
                      icon: Icons.error_outline,
                      title: s.t('history'),
                      message: c.historyError?.message ?? '',
                      action: s.t('retry'),
                      onAction: c.loadHistory)
                else if (c.historyPhase == LoadPhase.ready && items.isEmpty)
                  AnumEmpty(
                      icon: Icons.history,
                      title: s.t('history'),
                      message: s.t('historyEmpty'))
                else
                  for (final item in items)
                    AnumRow(
                      icon: item.status.finished
                          ? Icons.check_circle_outline
                          : Icons.autorenew,
                      tone: switch (item.status) {
                        CommandStatus.completed => AnumTone.ok,
                        CommandStatus.failed => AnumTone.stop,
                        CommandStatus.waitingApproval => AnumTone.warn,
                        CommandStatus.running => AnumTone.info,
                        _ => AnumTone.neutral,
                      },
                      title: item.text,
                      subtitle: s.f('stepsCount', {
                        'done': item.completedSteps,
                        'total': item.stepCount
                      }),
                      detail: clockTime(item.createdAt),
                      onTap: () => onOpen(item.id),
                      trailing: item.status.active
                          ? null
                          : IconButton(
                              tooltip: s.t('removeFromHistory'),
                              onPressed: () async {
                                final failure = await c.removeFromHistory(item);
                                if (failure != null && context.mounted) {
                                  ScaffoldMessenger.maybeOf(context)
                                      ?.showSnackBar(SnackBar(
                                          content: Text(failure.message)));
                                }
                              },
                              icon: const Icon(Icons.delete_outline)),
                    ),
                if (c.historyPhase == LoadPhase.loading)
                  const Padding(
                    padding: EdgeInsets.all(AnumSpacing.md),
                    child: Center(child: CircularProgressIndicator()),
                  )
                else if (c.historyHasMore)
                  TextButton(
                      onPressed: () => c.loadHistory(more: true),
                      child: Text(s.t('loadMore'))),
              ],
            ),
          );
        },
      );
}

class _ReportPage extends StatefulWidget {
  const _ReportPage({required this.controller});
  final CommandController controller;

  @override
  State<_ReportPage> createState() => _ReportPageState();
}

class _ReportPageState extends State<_ReportPage> {
  late Future<String> _report = widget.controller.reportText();

  @override
  Widget build(BuildContext context) {
    final s = CommandStrings.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(s.t('fullReport')),
        actions: [
          IconButton(
            tooltip: s.t('saveReport'),
            onPressed: () async {
              try {
                final saved = await widget.controller.saveReport();
                if (saved && context.mounted) {
                  ScaffoldMessenger.of(context)
                      .showSnackBar(SnackBar(content: Text(s.t('saved'))));
                }
              } on Object catch (error) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                      content: Text(CommandFailure.of(error).message)));
                }
              }
            },
            icon: const Icon(Icons.save_alt),
          ),
        ],
      ),
      body: FutureBuilder<String>(
        future: _report,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return AnumEmpty(
              icon: Icons.error_outline,
              title: s.t('fullReport'),
              message: CommandFailure.of(snapshot.error!).message,
              action: s.t('retry'),
              onAction: () =>
                  setState(() => _report = widget.controller.reportText()),
            );
          }
          if (!snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          return SingleChildScrollView(
            padding: const EdgeInsets.all(AnumSpacing.md),
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 820),
                child: SelectableText(snapshot.data!),
              ),
            ),
          );
        },
      ),
    );
  }
}
