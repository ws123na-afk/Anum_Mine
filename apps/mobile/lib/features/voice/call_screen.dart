import 'dart:async';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'call_controller.dart';
import 'call_models.dart';
import 'call_strings.dart';
import 'voice_models.dart';
import 'voice_orb.dart';

/// A photo from the device: its bytes and media type, or null when cancelled.
typedef PhotoPicker = Future<(Uint8List, String)?> Function();

Future<(Uint8List, String)?> pickDevicePhoto() async {
  final picked = await FilePicker.platform
      .pickFiles(type: FileType.image, withData: true, allowMultiple: false);
  final file = picked?.files.singleOrNull;
  final bytes = file?.bytes;
  if (file == null || bytes == null) return null;
  final extension = (file.extension ?? '').toLowerCase();
  final type = switch (extension) {
    'png' => 'image/png',
    'webp' => 'image/webp',
    _ => 'image/jpeg',
  };
  return (bytes, type);
}

/// The call screen of the mobile mockup (docs/voice.md#call-mode): dark,
/// with the call timer and status, the orb, Mute, Video, Speaker, Chat and
/// More, and a red End button. The call starts when the screen opens.
class CallScreen extends StatefulWidget {
  const CallScreen({
    required this.controller,
    required this.locale,
    required this.name,
    this.onOpenApprovals,
    this.onOpenConversation,
    this.pickPhoto = pickDevicePhoto,
    this.openSettings,
    super.key,
  });

  final CallController controller;
  final String locale;
  final String name;
  final VoidCallback? onOpenApprovals;
  final void Function(String conversationId)? onOpenConversation;
  final PhotoPicker pickPhoto;
  final Future<void> Function()? openSettings;

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  final _draft = TextEditingController();
  final _level = ValueNotifier<double>(0);
  Timer? _ticker;
  bool _chatOpen = false;
  bool _saving = false;

  CallController get call => widget.controller;

  @override
  void initState() {
    super.initState();
    call.addListener(_changed);
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && call.active) setState(() {});
    });
    unawaited(_start());
  }

  Future<void> _start() => call.start(locale: widget.locale, name: widget.name);

  void _changed() {
    if (!mounted) return;
    setState(() {});
  }

  @override
  void dispose() {
    _ticker?.cancel();
    call.removeListener(_changed);
    _draft.dispose();
    _level.dispose();
    super.dispose();
  }

  bool get _arabic => AnumLocalizations.maybeOf(context).isArabic;
  String _t(String key, [Map<String, Object> values = const {}]) =>
      callText(key, _arabic, values);

  VoiceState get _orbState => switch (call.phase) {
        CallPhase.listening => VoiceState.listening,
        CallPhase.thinking => VoiceState.thinking,
        CallPhase.speaking => VoiceState.speaking,
        _ => VoiceState.idle,
      };

  Future<void> _hangUp() async {
    call.hangUp();
    if (!call.hasConversation) {
      await call.finish(save: false);
    }
  }

  Future<void> _finish(bool save) async {
    setState(() => _saving = true);
    await call.finish(save: save);
    if (mounted) setState(() => _saving = false);
  }

  Future<void> _showPhoto() async {
    if (!call.capabilities.visionAvailable) {
      call.report(CallProblem.visionOff);
      return;
    }
    final pick = await showModalBottomSheet<bool>(
      context: context,
      showDragHandle: true,
      builder: (sheet) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.lg, 0, AnumSpacing.lg, AnumSpacing.lg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(_t('photoTitle', {'name': widget.name}),
                  style: Theme.of(sheet).textTheme.titleMedium),
              const SizedBox(height: AnumSpacing.xs),
              Text(_t('photoText', {'name': widget.name})),
              const SizedBox(height: AnumSpacing.md),
              FilledButton.icon(
                onPressed: () => Navigator.pop(sheet, true),
                icon: const Icon(Icons.photo_camera_outlined),
                label: Text(_t('photoPick')),
              ),
            ],
          ),
        ),
      ),
    );
    if (pick != true) return;
    final photo = await widget.pickPhoto();
    if (photo == null) return;
    unawaited(call.submit('', photo: photo.$1, photoType: photo.$2));
  }

  void _send() {
    final text = _draft.text.trim();
    if (text.isEmpty) return;
    _draft.clear();
    unawaited(call.submit(text));
  }

  Future<void> _showDetails() => showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (sheet) {
          final caps = call.capabilities;
          final latency = call.lastLatency;
          final target = (caps.firstAudioTargetMs / 1000).toStringAsFixed(1);
          return SafeArea(
            child: ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.fromLTRB(
                  AnumSpacing.lg, 0, AnumSpacing.lg, AnumSpacing.lg),
              children: [
                Text(_t('moreLabel'),
                    style: Theme.of(sheet).textTheme.titleMedium),
                ListTile(
                  title: Text(_t('detailsLatency')),
                  subtitle: Text(latency == null
                      ? _t('detailsLatencyNone', {'seconds': target})
                      : _t('detailsLatencyValue', {
                          'seconds': (latency.inMilliseconds / 1000)
                              .toStringAsFixed(1),
                          'target': target,
                        })),
                ),
                ListTile(
                  title: Text(_t('detailsBargeIns')),
                  subtitle: Text('${call.bargeIns}'),
                ),
                ListTile(
                  title: Text(_t('detailsEngine')),
                  subtitle: Text(_t('detailsEngineValue')),
                ),
                ListTile(
                  title: Text(_t('detailsCamera')),
                  subtitle: Text(caps.visionAvailable
                      ? (caps.visionModel ?? '')
                      : callProblemText(CallProblem.visionOff, _arabic,
                          visionReason: caps.visionReason)),
                ),
                Text(_t('detailsApprovals')),
              ],
            ),
          );
        },
      );

  @override
  Widget build(BuildContext context) => Theme(
        data: AnumTheme.dark(),
        child: Builder(builder: (context) {
          final scheme = Theme.of(context).colorScheme;
          return Scaffold(
            backgroundColor: const Color(0xFF0B1020),
            body: SafeArea(
              child: switch (call.phase) {
                CallPhase.ending => _saveChoice(context),
                CallPhase.ended => _ended(context),
                _ => LayoutBuilder(builder: (context, box) {
                    final wide = box.maxWidth >= AnumBreakpoints.expanded;
                    final stage = _stage(context, box);
                    return Column(
                      children: [
                        _header(context),
                        Expanded(
                          child: wide && _chatOpen
                              ? Row(children: [
                                  Expanded(child: stage),
                                  SizedBox(
                                      width: 360, child: _transcript(context)),
                                ])
                              // A phone shows the transcript in place of the
                              // orb; the timer and the controls stay.
                              : _chatOpen
                                  ? _transcript(context)
                                  : stage,
                        ),
                        _controls(context, scheme),
                      ],
                    );
                  }),
              },
            ),
          );
        }),
      );

  Widget _header(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final status = call.muted && call.active
        ? _t('status.muted')
        : _t('status.${call.phase.name}');
    final time = formatCallTime(call.elapsed);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
          AnumSpacing.md, AnumSpacing.sm, AnumSpacing.md, 0),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(widget.name, style: text.titleLarge),
                Wrap(children: [
                  Semantics(
                      liveRegion: true,
                      child: Text(status, style: text.bodyMedium)),
                  Text(' · ', style: text.bodyMedium),
                  Semantics(
                    label: _t('timer', {'time': time}),
                    excludeSemantics: true,
                    child: Text(time,
                        style: text.bodyMedium?.copyWith(
                            fontFeatures: const [FontFeature.tabularFigures()],
                            fontWeight: FontWeight.w600)),
                  ),
                ]),
              ],
            ),
          ),
          if (call.pendingApprovals > 0 && widget.onOpenApprovals != null)
            Flexible(
                child: TextButton(
              onPressed: widget.onOpenApprovals,
              child: Text(
                  _t('approvalsWaiting', {'count': call.pendingApprovals})),
            )),
        ],
      ),
    );
  }

  Widget _stage(BuildContext context, BoxConstraints box) {
    final text = Theme.of(context).textTheme;
    final last = call.turns.reversed
        .where((turn) =>
            turn.speaker == VoiceSpeaker.assistant && turn.text.isNotEmpty)
        .firstOrNull;
    final caption =
        call.caption.isNotEmpty ? call.caption : last?.text ?? _t('hint');
    final orb = (box.maxHeight * 0.3).clamp(120.0, 220.0);
    final problem = call.problem;
    return SingleChildScrollView(
      padding: const EdgeInsets.all(AnumSpacing.md),
      child: Column(
        children: [
          const SizedBox(height: AnumSpacing.md),
          Semantics(
            button: true,
            label: call.phase == CallPhase.speaking ||
                    call.phase == CallPhase.thinking
                ? _t('interrupt')
                : (call.muted ? _t('unmuteLabel') : _t('muteLabel')),
            child: GestureDetector(
              onTap: () => call.phase == CallPhase.speaking ||
                      call.phase == CallPhase.thinking
                  ? call.interrupt()
                  : call.setMuted(!call.muted),
              child: ExcludeSemantics(
                child: VoiceOrb(
                  state: _orbState,
                  level: _level,
                  alert: call.pendingApprovals > 0,
                  size: orb,
                ),
              ),
            ),
          ),
          const SizedBox(height: AnumSpacing.md),
          Text(caption,
              textAlign: TextAlign.center,
              maxLines: 6,
              overflow: TextOverflow.ellipsis,
              style: text.bodyLarge),
          if (problem != null) ...[
            const SizedBox(height: AnumSpacing.md),
            _problemCard(context, problem),
          ],
        ],
      ),
    );
  }

  Widget _problemCard(BuildContext context, CallProblem problem) {
    final scheme = Theme.of(context).colorScheme;
    return Semantics(
      liveRegion: true,
      child: Container(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        decoration: BoxDecoration(
          color: scheme.errorContainer.withValues(alpha: 0.25),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: scheme.error.withValues(alpha: 0.5)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(callProblemText(problem, _arabic,
                detail: call.problemDetail,
                visionReason: call.capabilities.visionReason)),
            Wrap(
              spacing: AnumSpacing.xs,
              children: [
                if (problem == CallProblem.micDenied &&
                    widget.openSettings != null)
                  TextButton(
                      onPressed: widget.openSettings,
                      child: Text(_t('openSettings'))),
                if (problem == CallProblem.micDenied ||
                    call.phase == CallPhase.idle)
                  TextButton(onPressed: _start, child: Text(_t('tryAgain'))),
                if (problem == CallProblem.speechUnavailable)
                  TextButton(
                      onPressed: () => setState(() => _chatOpen = true),
                      child: Text(_t('chat'))),
                if (problem == CallProblem.limit ||
                    problem == CallProblem.session)
                  TextButton(onPressed: _hangUp, child: Text(_t('end'))),
                TextButton(
                    onPressed: call.clearProblem, child: Text(_t('close'))),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _transcript(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Card(
      margin: const EdgeInsets.all(AnumSpacing.sm),
      child: Column(
        children: [
          ListTile(
            title: Text(_t('transcript'), style: text.titleMedium),
            trailing: IconButton(
              tooltip: _t('close'),
              icon: const Icon(Icons.close),
              onPressed: () => setState(() => _chatOpen = false),
            ),
          ),
          Expanded(
            child: call.turns.isEmpty
                ? Padding(
                    padding: const EdgeInsets.all(AnumSpacing.md),
                    child: Text(_t('empty')),
                  )
                : ListView(
                    padding:
                        const EdgeInsets.symmetric(horizontal: AnumSpacing.md),
                    children: [
                      for (final turn in call.turns)
                        if (turn.text.isNotEmpty) _turn(context, turn),
                    ],
                  ),
          ),
          Padding(
            padding: const EdgeInsets.all(AnumSpacing.sm),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _draft,
                  minLines: 1,
                  maxLines: 3,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _send(),
                  decoration: InputDecoration(
                      hintText: _t('typeTo', {'name': widget.name}),
                      labelText: _t('typeTo', {'name': widget.name})),
                ),
              ),
              IconButton.filled(
                tooltip: _t('send'),
                onPressed: _send,
                icon: const Icon(Icons.arrow_upward),
              ),
            ]),
          ),
        ],
      ),
    );
  }

  Widget _turn(BuildContext context, VoiceTurn turn) {
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(turn.speaker == VoiceSpeaker.you ? _t('you') : widget.name,
              style: text.labelSmall),
          Text(turn.text),
          if (turn.awaitingConfirmation)
            Wrap(spacing: AnumSpacing.xs, children: [
              FilledButton(
                onPressed: () => unawaited(call.confirmTask(turn)),
                child: Text(_t('createTask')),
              ),
              TextButton(
                  onPressed: () => call.dismiss(turn),
                  child: Text(_t('notNow'))),
            ]),
          if (turn.resolution == TurnResolution.created)
            Text(_t('taskCreated'), style: text.labelMedium),
          if (turn.result?.riskTier == VoiceRiskTier.visualOnly &&
              widget.onOpenApprovals != null)
            TextButton(
                onPressed: widget.onOpenApprovals,
                child: Text(_t('openApprovals'))),
        ],
      ),
    );
  }

  Widget _controls(BuildContext context, ColorScheme scheme) => Padding(
        padding: const EdgeInsets.fromLTRB(
            AnumSpacing.sm, AnumSpacing.xs, AnumSpacing.sm, AnumSpacing.md),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                _control(
                    icon: call.muted ? Icons.mic_off : Icons.mic,
                    label: call.muted ? _t('unmute') : _t('mute'),
                    semantics: call.muted ? _t('unmuteLabel') : _t('muteLabel'),
                    selected: call.muted,
                    onTap: () => call.setMuted(!call.muted)),
                _control(
                    icon: Icons.videocam_outlined,
                    label: _t('video'),
                    semantics: _t('videoLabel'),
                    selected: false,
                    onTap: _showPhoto),
                _control(
                    icon: call.speakerOn ? Icons.volume_up : Icons.volume_off,
                    label: _t('speaker'),
                    semantics: call.speakerOn
                        ? _t('speakerOffLabel')
                        : _t('speakerOnLabel'),
                    selected: call.speakerOn,
                    onTap: () => call.setSpeaker(!call.speakerOn)),
                _control(
                    icon: Icons.chat_bubble_outline,
                    label: _t('chat'),
                    semantics: _t('chatLabel'),
                    selected: _chatOpen,
                    onTap: () => setState(() => _chatOpen = !_chatOpen)),
                _control(
                    icon: Icons.more_horiz,
                    label: _t('more'),
                    semantics: _t('moreLabel'),
                    selected: false,
                    onTap: _showDetails),
              ],
            ),
            const SizedBox(height: AnumSpacing.md),
            ConstrainedBox(
              constraints: const BoxConstraints(minWidth: 200, minHeight: 52),
              child: FilledButton.icon(
                style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFFC92A3A),
                    foregroundColor: Colors.white),
                onPressed: call.active ? _hangUp : null,
                icon: const Icon(Icons.call_end),
                label: Text(_t('end')),
              ),
            ),
          ],
        ),
      );

  Widget _control({
    required IconData icon,
    required String label,
    required String semantics,
    required bool selected,
    required VoidCallback onTap,
  }) =>
      Flexible(
        child: Semantics(
          button: true,
          toggled: selected,
          label: semantics,
          excludeSemantics: true,
          child: InkWell(
            borderRadius: BorderRadius.circular(32),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.xxs),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                CircleAvatar(
                  radius: 26,
                  backgroundColor: selected
                      ? Colors.white
                      : Colors.white.withValues(alpha: 0.1),
                  foregroundColor:
                      selected ? const Color(0xFF0B1020) : Colors.white,
                  child: Icon(icon),
                ),
                const SizedBox(height: AnumSpacing.xxs),
                Text(label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.labelSmall),
              ]),
            ),
          ),
        ),
      );

  Widget _saveChoice(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AnumSpacing.lg),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(_t('timer', {'time': formatCallTime(call.elapsed)}),
                  style: text.bodyMedium),
              const SizedBox(height: AnumSpacing.xs),
              Semantics(
                  header: true,
                  child: Text(_t('saveTitle'), style: text.headlineSmall)),
              const SizedBox(height: AnumSpacing.sm),
              Text(_t('saveText')),
              if (call.problem == CallProblem.summary) ...[
                const SizedBox(height: AnumSpacing.sm),
                _problemCard(context, CallProblem.summary),
              ],
              const SizedBox(height: AnumSpacing.lg),
              FilledButton(
                onPressed: _saving ? null : () => _finish(true),
                child: Text(_saving ? _t('saving') : _t('save')),
              ),
              const SizedBox(height: AnumSpacing.xs),
              OutlinedButton(
                onPressed: _saving ? null : () => _finish(false),
                child: Text(_t('discard')),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _ended(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final summary = call.summary;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AnumSpacing.lg),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(_t('timer', {'time': formatCallTime(call.elapsed)}),
                  style: text.bodyMedium),
              Semantics(
                  header: true,
                  child: Text(_t('endedTitle'), style: text.headlineSmall)),
              const SizedBox(height: AnumSpacing.sm),
              if (summary != null) ...[
                Text(summary.fromModel
                    ? _t('summaryByModel')
                    : _t('summaryFromTranscript')),
                const SizedBox(height: AnumSpacing.xs),
                Text(summary.summary),
              ] else
                Text(_t('nothingKept')),
              const SizedBox(height: AnumSpacing.lg),
              if (summary?.conversationId != null &&
                  widget.onOpenConversation != null)
                FilledButton(
                  onPressed: () =>
                      widget.onOpenConversation!(summary!.conversationId!),
                  child: Text(_t('openConversation')),
                ),
              const SizedBox(height: AnumSpacing.xs),
              OutlinedButton(
                onPressed: () => Navigator.maybePop(context),
                child: Text(_t('close')),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Opens a call over the current screen. [prepare] runs first (the Voice
/// tab stops listening for its wake name), [restore] after the call closes.
Future<void> openCall(
  BuildContext context, {
  required CallController Function() create,
  required String locale,
  required String name,
  VoidCallback? prepare,
  VoidCallback? restore,
  VoidCallback? onOpenApprovals,
  Future<void> Function()? openSettings,
}) async {
  prepare?.call();
  final controller = create();
  try {
    await Navigator.of(context).push(MaterialPageRoute<void>(
      fullscreenDialog: true,
      builder: (route) => CallScreen(
        controller: controller,
        locale: locale,
        name: name,
        openSettings: openSettings,
        onOpenApprovals: onOpenApprovals == null
            ? null
            : () {
                Navigator.of(route).maybePop();
                onOpenApprovals();
              },
      ),
    ));
  } finally {
    controller.dispose();
    restore?.call();
  }
}
