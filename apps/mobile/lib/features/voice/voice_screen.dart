import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'voice_controller.dart';
import 'voice_models.dart';
import 'voice_orb.dart';

/// Talk to the assistant by name, by tapping the orb, or by typing.
class VoiceScreen extends StatefulWidget {
  const VoiceScreen({
    required this.controller,
    this.onOpenApprovals,
    super.key,
  });

  final VoiceController controller;

  /// Opens the Approvals tab. Voice never approves anything itself.
  final VoidCallback? onOpenApprovals;

  @override
  State<VoiceScreen> createState() => _VoiceScreenState();
}

class _VoiceScreenState extends State<VoiceScreen> with WidgetsBindingObserver {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  bool _onScreen = true;
  bool _resumed = true;
  int _turnCount = 0;

  VoiceController get controller => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    final lifecycle = WidgetsBinding.instance.lifecycleState;
    _resumed = lifecycle == null || lifecycle == AppLifecycleState.resumed;
    controller.addListener(_onControllerChanged);
    _turnCount = controller.turns.length;
    controller.load();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Inside the workspace IndexedStack the screen stays mounted; listen for
    // the name only while this tab is the one on screen.
    _onScreen = Visibility.of(context);
    _syncVisibility();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _resumed = state == AppLifecycleState.resumed;
    _syncVisibility();
  }

  void _syncVisibility() {
    final visible = _onScreen && _resumed;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) controller.setVisible(visible);
    });
  }

  void _onControllerChanged() {
    if (controller.turns.length == _turnCount) return;
    _turnCount = controller.turns.length;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      final end = _scroll.position.maxScrollExtent;
      if (MediaQuery.maybeDisableAnimationsOf(context) ?? false) {
        _scroll.jumpTo(end);
      } else {
        _scroll.animateTo(end,
            duration: const Duration(milliseconds: 280),
            curve: Curves.easeOutCubic);
      }
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    controller.removeListener(_onControllerChanged);
    final voice = controller;
    Future.microtask(() => voice.setVisible(false));
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _send() {
    final text = _input.text.trim();
    if (text.isEmpty) return;
    _input.clear();
    controller.ask(text);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final width = MediaQuery.sizeOf(context).width;
          final gutter = width < AnumBreakpoints.compact ? 16.0 : 28.0;
          return AnumBackdrop(
            child: SafeArea(
              bottom: false,
              child: Column(children: [
                Padding(
                  padding: EdgeInsets.fromLTRB(gutter, 12, gutter * 0.5, 4),
                  child: _VoiceHeader(controller: controller),
                ),
                Expanded(
                  child: ListView(
                    controller: _scroll,
                    padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 24),
                    children: [
                      Center(
                        child: ConstrainedBox(
                          constraints: const BoxConstraints(maxWidth: 720),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              _Stage(
                                controller: controller,
                                orbSize:
                                    width < AnumBreakpoints.compact ? 190 : 230,
                                onOpenApprovals: widget.onOpenApprovals,
                              ),
                              const SizedBox(height: AnumSpacing.lg),
                              _Conversation(
                                controller: controller,
                                onOpenApprovals: widget.onOpenApprovals,
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                _Dock(
                  controller: controller,
                  input: _input,
                  onSend: _send,
                  gutter: gutter,
                ),
              ]),
            ),
          );
        },
      );
}

class _VoiceHeader extends StatelessWidget {
  const _VoiceHeader({required this.controller});
  final VoiceController controller;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final name = controller.assistantName;
    final arabic = controller.isArabic;
    return Row(children: [
      ExcludeSemantics(
        child: Container(
          width: 40,
          height: 40,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: const LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [orbSky, orbViolet, orbRose],
            ),
            boxShadow: [
              BoxShadow(
                  color: orbViolet.withValues(alpha: 0.35), blurRadius: 14),
            ],
          ),
          child: Text(
            name.characters.first.toUpperCase(),
            style: TextStyle(
                color: p.background, fontWeight: FontWeight.w800, fontSize: 17),
          ),
        ),
      ),
      const SizedBox(width: 12),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  color: p.text, fontSize: 19, fontWeight: FontWeight.w700)),
          Text(arabic ? 'مساعدتك الصوتية' : 'Your voice assistant',
              style: TextStyle(color: p.muted, fontSize: 12.5)),
        ]),
      ),
      _WakeToggle(controller: controller),
      IconButton(
        tooltip:
            controller.muted ? 'Unmute spoken replies' : 'Mute spoken replies',
        onPressed: controller.toggleMute,
        color: controller.muted ? p.warn : p.muted,
        icon: Icon(controller.muted
            ? Icons.volume_off_rounded
            : Icons.volume_up_rounded),
      ),
      IconButton(
        tooltip: 'Voice settings',
        onPressed: () => _showVoiceSettings(context, controller),
        color: p.muted,
        icon: const Icon(Icons.tune_rounded),
      ),
    ]);
  }
}

class _WakeToggle extends StatelessWidget {
  const _WakeToggle({required this.controller});
  final VoiceController controller;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final on = controller.wakeEnabled;
    final color = on ? p.sky : p.muted;
    final name = controller.assistantName;
    return Semantics(
      toggled: on,
      button: true,
      label: on
          ? 'Wake by name is on. Saying $name wakes the assistant.'
          : 'Wake by name is off',
      excludeSemantics: true,
      child: Tooltip(
        message: on
            ? 'Listening for “$name” while this screen is open'
            : 'Turn on wake by name',
        child: InkWell(
          onTap: controller.toggleWake,
          borderRadius: BorderRadius.circular(999),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 40),
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: color.withValues(alpha: on ? 0.14 : 0.06),
                borderRadius: BorderRadius.circular(999),
                border: Border.all(color: color.withValues(alpha: 0.3)),
              ),
              child: Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                child: Row(mainAxisSize: MainAxisSize.min, children: [
                  Icon(on ? Icons.hearing_rounded : Icons.hearing_disabled,
                      size: 16, color: color),
                  const SizedBox(width: 6),
                  Text(on ? 'Wake on' : 'Wake off',
                      style: TextStyle(
                          color: color,
                          fontSize: 12,
                          fontWeight: FontWeight.w700)),
                ]),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Stage extends StatelessWidget {
  const _Stage({
    required this.controller,
    required this.orbSize,
    this.onOpenApprovals,
  });

  final VoiceController controller;
  final double orbSize;
  final VoidCallback? onOpenApprovals;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final state = controller.state;
    final name = controller.assistantName;
    final arabic = controller.isArabic;
    final wakeReady = controller.wakeEnabled &&
        !controller.micBlocked &&
        !controller.speechUnavailable;
    final stateText = switch (state) {
      VoiceState.idle => wakeReady
          ? (arabic ? 'أستمع لاسم "$name"' : 'Listening for “$name”')
          : (arabic ? 'جاهزة' : 'Ready'),
      VoiceState.listening => controller.followUp
          ? (arabic ? 'تفضل، أنا أستمع' : "Go ahead, I'm listening")
          : (arabic ? 'أنا أستمع' : "I'm listening"),
      VoiceState.thinking => arabic ? 'لحظة واحدة' : 'One moment',
      VoiceState.speaking => arabic ? '$name تتحدث' : '$name is speaking',
    };
    final idleHint = wakeReady
        ? (arabic
            ? 'قل "$name" أو اضغط على الكرة.'
            : 'Say “$name” or tap the orb.')
        : (arabic ? 'اضغط على الكرة وتحدث.' : 'Tap the orb and just talk.');
    final detail = controller.notice ??
        switch (state) {
          VoiceState.idle => idleHint,
          VoiceState.listening =>
            controller.heard.isEmpty ? '…' : controller.heard,
          _ => '',
        };
    final orbLabel = switch (state) {
      VoiceState.listening => 'Stop listening and send',
      VoiceState.speaking => 'Stop speaking',
      VoiceState.thinking => '$name is thinking',
      VoiceState.idle => 'Tap to talk to $name',
    };
    final icon = switch (state) {
      VoiceState.listening => Icons.stop_rounded,
      VoiceState.speaking => Icons.graphic_eq_rounded,
      VoiceState.thinking => null,
      VoiceState.idle => Icons.mic_rounded,
    };
    final pending = controller.pendingApprovals;

    return Column(children: [
      Semantics(
        button: true,
        enabled: state != VoiceState.thinking,
        label: orbLabel,
        excludeSemantics: true,
        child: GestureDetector(
          onTap: state == VoiceState.thinking ? null : controller.tapOrb,
          child: Stack(alignment: Alignment.center, children: [
            VoiceOrb(
              state: state,
              level: controller.level,
              alert: pending > 0,
              size: orbSize,
              active: controller.isVisible,
            ),
            if (icon != null)
              Icon(icon,
                  size: orbSize * 0.16,
                  color: Colors.white.withValues(alpha: 0.92),
                  shadows: const [
                    Shadow(color: Color(0x66000000), blurRadius: 8)
                  ]),
          ]),
        ),
      ),
      const SizedBox(height: 4),
      Semantics(
        liveRegion: true,
        child: Text(stateText,
            textAlign: TextAlign.center,
            style: TextStyle(
                color: p.text, fontSize: 17, fontWeight: FontWeight.w700)),
      ),
      const SizedBox(height: 6),
      Text(detail,
          textAlign: TextAlign.center,
          maxLines: 4,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
              color: state == VoiceState.listening ? p.text : p.muted,
              fontSize: 14,
              height: 1.35)),
      if (pending > 0) ...[
        const SizedBox(height: AnumSpacing.sm),
        _ApprovalsChip(count: pending, onTap: onOpenApprovals),
      ],
      if (controller.micBlocked) ...[
        const SizedBox(height: AnumSpacing.md),
        _MicRecovery(controller: controller),
      ],
    ]);
  }
}

class _ApprovalsChip extends StatelessWidget {
  const _ApprovalsChip({required this.count, this.onTap});
  final int count;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final label = count == 1
        ? '1 approval waiting for you'
        : '$count approvals waiting for you';
    final color = context.palette.warn;
    final pill = DecoratedBox(
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.32)),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.approval_outlined, size: 15, color: color),
          const SizedBox(width: 6),
          Flexible(
            child: Text(label,
                style: TextStyle(
                    color: color, fontSize: 12.5, fontWeight: FontWeight.w700)),
          ),
          if (onTap != null) Icon(Icons.chevron_right, size: 16, color: color),
        ]),
      ),
    );
    if (onTap == null) return pill;
    return Semantics(
      button: true,
      label: '$label. Open approvals',
      excludeSemantics: true,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(999),
        child: Padding(padding: const EdgeInsets.all(6), child: pill),
      ),
    );
  }
}

class _MicRecovery extends StatelessWidget {
  const _MicRecovery({required this.controller});
  final VoiceController controller;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return AnumSurface(
      accent: p.warn,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Microphone access is off',
            style: TextStyle(
                color: p.text, fontSize: 15, fontWeight: FontWeight.w700)),
        const SizedBox(height: 4),
        Text(
            'Allow the microphone for ANUM in your device settings to talk to '
            '${controller.assistantName}. Typing works either way.',
            style: TextStyle(color: p.muted, fontSize: 13.5)),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: 8, runSpacing: 8, children: [
          FilledButton.tonalIcon(
              onPressed: controller.openSettings,
              icon: const Icon(Icons.settings_outlined),
              label: const Text('Open app settings')),
          TextButton(
              onPressed: controller.retryMicrophone,
              child: const Text('Try again')),
        ]),
      ]),
    );
  }
}

class _Conversation extends StatelessWidget {
  const _Conversation({required this.controller, this.onOpenApprovals});
  final VoiceController controller;
  final VoidCallback? onOpenApprovals;

  @override
  Widget build(BuildContext context) {
    final arabic = controller.isArabic;
    if (controller.turns.isEmpty) {
      return AnumSurface(
        child: AnumEmpty(
          icon: Icons.forum_outlined,
          title: arabic ? 'جرّب أن تقول' : 'Try saying',
          message: arabic
              ? '"ما اسمك؟"\n"ما حالة مهامي؟"\n"أنشئ مهمة: تجهيز التقرير الأسبوعي"'
              : '“What\'s your name?”\n“What\'s running?”\n“Create a task: draft the weekly report”',
        ),
      );
    }
    return Semantics(
      label: 'Conversation',
      container: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final turn in controller.turns)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _Bubble(
                turn: turn,
                controller: controller,
                onOpenApprovals: onOpenApprovals,
              ),
            ),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({
    required this.turn,
    required this.controller,
    this.onOpenApprovals,
  });

  final VoiceTurn turn;
  final VoiceController controller;
  final VoidCallback? onOpenApprovals;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final mine = turn.speaker == VoiceSpeaker.you;
    final maxWidth = MediaQuery.sizeOf(context).width * 0.82;
    final radius = BorderRadiusDirectional.only(
      topStart: const Radius.circular(18),
      topEnd: const Radius.circular(18),
      bottomStart: Radius.circular(mine ? 18 : 6),
      bottomEnd: Radius.circular(mine ? 6 : 18),
    );
    final body = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (!mine) ...[
          Text(controller.assistantName,
              style: TextStyle(
                  color: p.sky,
                  fontSize: 11.5,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.3)),
          const SizedBox(height: 4),
        ],
        SelectableText(turn.text,
            style: TextStyle(color: p.text, fontSize: 15, height: 1.4)),
        if (!mine && turn.result != null) ..._actions(context, turn.result!),
      ],
    );
    return Align(
      alignment: mine
          ? AlignmentDirectional.centerEnd
          : AlignmentDirectional.centerStart,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth.clamp(220, 560)),
        child: DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: radius,
            gradient: mine
                ? LinearGradient(colors: [
                    p.sky.withValues(alpha: 0.22),
                    p.violet.withValues(alpha: 0.22),
                  ])
                : LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [p.surface3, p.surface2],
                  ),
            border: Border.all(color: mine ? p.edge : p.line),
            boxShadow: [
              BoxShadow(
                  color: p.shadow, blurRadius: 12, offset: const Offset(0, 4)),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
            child: body,
          ),
        ),
      ),
    );
  }

  List<Widget> _actions(BuildContext context, VoiceAskResult result) {
    final p = context.palette;
    switch (result.riskTier) {
      case VoiceRiskTier.read:
        return const [];
      case VoiceRiskTier.confirm:
        return [
          const SizedBox(height: 10),
          const AnumPill(
              label: 'Needs your OK',
              tone: AnumTone.warn,
              icon: Icons.touch_app_outlined),
          if (turn.awaitingConfirmation) ...[
            const SizedBox(height: 10),
            Text('Review command',
                style: TextStyle(
                    color: p.muted, fontSize: 12, fontWeight: FontWeight.w600)),
            const SizedBox(height: 2),
            Text('“${result.proposedTask}”',
                style: TextStyle(
                    color: p.text, fontSize: 14, fontWeight: FontWeight.w600)),
            const SizedBox(height: 10),
            Wrap(spacing: 8, runSpacing: 8, children: [
              FilledButton.icon(
                onPressed:
                    turn.busy ? null : () => controller.confirmTask(turn),
                icon: turn.busy
                    ? const SizedBox.square(
                        dimension: 16,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.check_rounded),
                label: const Text('Create task'),
              ),
              TextButton(
                onPressed: turn.busy ? null : () => controller.dismiss(turn),
                child: const Text('Not now'),
              ),
            ]),
          ],
          if (turn.resolution == TurnResolution.created) ...[
            const SizedBox(height: 8),
            const AnumPill(
                label: 'Task created',
                tone: AnumTone.ok,
                icon: Icons.check_circle_outline),
          ],
          if (turn.resolution == TurnResolution.dismissed) ...[
            const SizedBox(height: 8),
            Text('Not created',
                style: TextStyle(color: p.faint, fontSize: 12.5)),
          ],
        ];
      case VoiceRiskTier.visualOnly:
        return [
          const SizedBox(height: 10),
          const AnumPill(
              label: 'On screen only',
              tone: AnumTone.accent,
              icon: Icons.shield_outlined),
          const SizedBox(height: 6),
          Text(
              'Approvals, deletes and payments need your visual approval on screen.',
              style: TextStyle(color: p.muted, fontSize: 12.5)),
          if (onOpenApprovals != null) ...[
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: onOpenApprovals,
              icon: const Icon(Icons.approval_outlined),
              label: const Text('Open approvals'),
            ),
          ],
        ];
    }
  }
}

class _Dock extends StatelessWidget {
  const _Dock({
    required this.controller,
    required this.input,
    required this.onSend,
    required this.gutter,
  });

  final VoiceController controller;
  final TextEditingController input;
  final VoidCallback onSend;
  final double gutter;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final busy = controller.state == VoiceState.thinking ||
        controller.state == VoiceState.listening;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: p.surface1,
        border: Border(top: BorderSide(color: p.line)),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: EdgeInsets.fromLTRB(gutter, 10, gutter, 10),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: input,
                enabled: controller.state != VoiceState.listening,
                minLines: 1,
                maxLines: 4,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => onSend(),
                style: TextStyle(color: p.text),
                decoration: InputDecoration(
                  hintText: controller.isArabic
                      ? 'أو اكتب رسالتك هنا'
                      : 'Or type a message',
                  hintStyle: TextStyle(color: p.faint),
                  filled: true,
                  fillColor: p.surface2,
                  isDense: true,
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(14),
                    borderSide: BorderSide(color: p.line),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(14),
                    borderSide: BorderSide(color: p.line),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            ValueListenableBuilder<TextEditingValue>(
              valueListenable: input,
              builder: (context, value, _) => FilledButton(
                onPressed: value.text.trim().isEmpty || busy ? null : onSend,
                style: FilledButton.styleFrom(
                    minimumSize: const Size(64, 46),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(14))),
                child: const Text('Send'),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

Future<void> _showVoiceSettings(
        BuildContext context, VoiceController controller) =>
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      backgroundColor: context.palette.surface1,
      builder: (context) => _VoiceSettingsSheet(controller: controller),
    );

class _VoiceSettingsSheet extends StatefulWidget {
  const _VoiceSettingsSheet({required this.controller});
  final VoiceController controller;

  @override
  State<_VoiceSettingsSheet> createState() => _VoiceSettingsSheetState();
}

class _VoiceSettingsSheetState extends State<_VoiceSettingsSheet> {
  late final TextEditingController _name =
      TextEditingController(text: widget.controller.assistantName);
  String? _nameError;

  VoiceController get controller => widget.controller;

  @override
  void dispose() {
    // Closing the sheet keeps a valid edited name. Deferred: listeners must
    // not rebuild while the tree is being torn down.
    final name = _name.text;
    final voice = controller;
    Future.microtask(() => voice.setAssistantName(name));
    _name.dispose();
    super.dispose();
  }

  Future<void> _saveName() async {
    final saved = await controller.setAssistantName(_name.text);
    if (!mounted) return;
    setState(() => _nameError =
        saved ? null : 'Use letters, numbers and spaces (up to 40).');
    if (saved) _name.text = controller.assistantName;
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final label = TextStyle(
        color: p.muted,
        fontSize: 12,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.4);
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SafeArea(
        child: ListenableBuilder(
          listenable: controller,
          builder: (context, _) => SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const AnumHeader(
                    title: 'Voice settings',
                    eyebrow: 'Assistant',
                    subtitle: 'Saved on this device.'),
                const SizedBox(height: AnumSpacing.md),
                Text('ASSISTANT NAME', style: label),
                const SizedBox(height: 6),
                TextField(
                  controller: _name,
                  maxLength: 40,
                  textInputAction: TextInputAction.done,
                  onSubmitted: (_) => _saveName(),
                  style: TextStyle(color: p.text),
                  decoration: InputDecoration(
                    hintText: 'Anum',
                    errorText: _nameError,
                    helperText:
                        'Say this name to wake the assistant. Ask "what\'s your name?" to hear it.',
                    helperMaxLines: 2,
                    suffixIcon: IconButton(
                        tooltip: 'Save name',
                        onPressed: _saveName,
                        icon: const Icon(Icons.check_rounded)),
                  ),
                ),
                const SizedBox(height: AnumSpacing.sm),
                Text('LANGUAGE', style: label),
                RadioGroup<String>(
                  groupValue: controller.locale,
                  onChanged: (value) {
                    if (value != null) controller.setLocale(value);
                  },
                  child: const Column(children: [
                    RadioListTile<String>(
                        value: 'en-US',
                        contentPadding: EdgeInsets.zero,
                        title: Text('English (US)')),
                    RadioListTile<String>(
                        value: 'ar-SA',
                        contentPadding: EdgeInsets.zero,
                        title: Text('Arabic (Saudi Arabia)'),
                        subtitle: Text('العربية (السعودية)')),
                  ]),
                ),
                const SizedBox(height: AnumSpacing.xs),
                Text('TRANSCRIPT RETENTION', style: label),
                RadioGroup<VoiceRetention>(
                  groupValue: controller.retention,
                  onChanged: (value) {
                    if (value != null) controller.setRetention(value);
                  },
                  child: Column(children: [
                    for (final value in VoiceRetention.values)
                      RadioListTile<VoiceRetention>(
                          value: value,
                          contentPadding: EdgeInsets.zero,
                          title: Text(value.label)),
                  ]),
                ),
                if (controller.session != null)
                  Text(
                      'A new retention choice applies to the next conversation.',
                      style: TextStyle(color: p.faint, fontSize: 12)),
                const SizedBox(height: AnumSpacing.md),
                AnumSurface(
                  accent: p.violet,
                  child: Text(
                      '${controller.assistantName} never approves, deletes or pays by voice. '
                      'Those always need your visual approval on screen. Wake by name only '
                      'listens while the Voice screen is open, using your device speech service.',
                      style: TextStyle(color: p.muted, fontSize: 13)),
                ),
                const SizedBox(height: AnumSpacing.sm),
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: TextButton.icon(
                    onPressed:
                        controller.turns.isEmpty && controller.session == null
                            ? null
                            : () async {
                                await controller.clearConversation();
                                if (context.mounted) Navigator.pop(context);
                              },
                    style: TextButton.styleFrom(foregroundColor: p.stop),
                    icon: const Icon(Icons.delete_outline),
                    label: const Text('Discard transcript'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
