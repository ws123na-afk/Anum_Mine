import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/brand.dart';
import '../../src/widgets/depth.dart';
import 'home_controller.dart';
import 'home_models.dart';
import 'home_sheets.dart';
import 'home_widgets.dart';
import 'insights_screen.dart';

Future<bool> _launch(Uri uri) =>
    launchUrl(uri, mode: LaunchMode.externalApplication);

/// The ANUM 360 Home (docs/home.md, mockups "Home / Command" and "Home
/// Dashboard"): greeting, ask box with mic, Today's Focus, Recent Activity,
/// Work/Personal/Custom quick commands, shortcut tiles, "Your AI agent is
/// ready" and pinned apps (two columns from 840 px). Everything comes from the API; the
/// shell decides where each feature opens through the callbacks.
class HomeScreen extends StatefulWidget {
  const HomeScreen({
    required this.controller,
    required this.onCommand,
    required this.onVoice,
    required this.onOpen,
    required this.onSwitchWorkspace,
    this.availableFeatures,
    this.header,
    this.sections = const [],
    this.onOpenLink = _launch,
    this.clock = DateTime.now,
    super.key,
  });

  final HomeController controller;

  /// Opens ANUM Command; [text] is the typed request or a quick command
  /// (null: an empty command screen).
  final ValueChanged<String?> onCommand;

  /// Opens ANUM Command listening (the mic).
  final VoidCallback onVoice;

  /// Opens a feature (`tasks`, `approvals`, `automations`, ...) and, when
  /// given, the item in it.
  final ValueChanged<HomeTarget> onOpen;

  /// Switches to a pinned workspace.
  final ValueChanged<String> onSwitchWorkspace;

  /// Feature keys the shell can open; tiles and pins for others are hidden,
  /// so nothing on Home is a dead button. Null: every feature.
  final Set<String>? availableFeatures;

  /// The shell's greeting header (avatar, name, date, bell). When given it
  /// replaces Home's own greeting, and the insights entry moves under it.
  /// `SizedBox.shrink()` when the shell shows its header above the page.
  final Widget? header;

  /// The shell's own Home cards (favorites, tyre planning), shown after
  /// Today's Focus.
  final List<Widget> sections;

  /// Opens a pinned web app (url_launcher; tests pass a fake).
  final Future<bool> Function(Uri uri) onOpenLink;
  final DateTime Function() clock;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _ask = TextEditingController();

  HomeController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.addListener(_onChange);
    if (c.phase == HomePhase.idle) c.load();
  }

  @override
  void didUpdateWidget(HomeScreen old) {
    super.didUpdateWidget(old);
    if (old.controller != c) {
      old.controller.removeListener(_onChange);
      c.addListener(_onChange);
    }
  }

  @override
  void dispose() {
    c.removeListener(_onChange);
    _ask.dispose();
    super.dispose();
  }

  void _onChange() {
    if (!mounted) return;
    final error = c.noticeIsError;
    final notice = c.takeNotice();
    if (notice != null) {
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(
        content: Text(notice),
        backgroundColor: error ? context.palette.surface3 : null,
      ));
    }
    setState(() {});
  }

  bool _can(String feature) =>
      widget.availableFeatures == null ||
      widget.availableFeatures!.contains(feature);

  void _submit() {
    final text = _ask.text.trim();
    if (text.isEmpty) return;
    _ask.clear();
    widget.onCommand(text);
  }

  /// Opens a target; ANUM Proactive and ANUM Command are Home's own.
  void _open(HomeTarget target) => switch (target.feature) {
        'insights' => _openInsights(),
        'command' => widget.onCommand(null),
        _ => widget.onOpen(target),
      };

  void _openInsights() {
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => ProactiveInsightsScreen(
        controller: c,
        onOpen: _open,
        onCommand: widget.onCommand,
      ),
    ));
  }

  Future<void> _openPin(HomePin pin) async {
    final t = AnumLocalizations.maybeOf(context);
    switch (pin.kind) {
      case PinKind.link:
        final uri = Uri.tryParse(pin.target);
        var opened = false;
        if (uri != null && uri.scheme == 'https') {
          try {
            opened = await widget.onOpenLink(uri);
          } on Object {
            opened = false;
          }
        }
        if (!opened && mounted) {
          ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(
              content: Text(t.f('homeOpenFailed', {'name': pin.label}))));
        }
      case PinKind.workspace:
        widget.onSwitchWorkspace(pin.target);
      case PinKind.feature:
        _open(HomeTarget(pin.target));
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    if (c.phase == HomePhase.denied) {
      return AnumPage(onRefresh: c.load, children: [
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.lock_outline,
            title: t.t('homeDeniedTitle'),
            message: t.f('homeDeniedHint', {'message': c.error ?? ''}),
            action: t.t('retry'),
            onAction: c.load,
          ),
        ),
      ]);
    }
    return LayoutBuilder(builder: (context, constraints) {
      final wide = constraints.maxWidth >= AnumBreakpoints.expanded;
      final header = widget.header;
      final Widget greeting = header == null
          ? _Greeting(
              controller: c,
              now: widget.clock(),
              onInsights: _openInsights,
            )
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                header,
                _InsightsEntry(controller: c, onOpen: _openInsights),
              ],
            );
      final ask = _AskSection(
        ask: _ask,
        onSubmit: _submit,
        onVoice: widget.onVoice,
      );
      final commands =
          _CommandSection(controller: c, onCommand: widget.onCommand);
      final shortcuts = _shortcuts(t);
      final agentReady = _AgentReady(onTap: widget.onVoice);
      final focus = _FocusSection(
        controller: c,
        onOpen: _open,
        onCommand: () => widget.onCommand(null),
        canOpenTasks: _can('tasks'),
        now: widget.clock(),
      );
      final activity = _ActivitySection(
        controller: c,
        onOpen: _open,
        onCommand: () => widget.onCommand(null),
        canOpenTasks: _can('tasks'),
        now: widget.clock(),
      );
      final pinned = _PinnedSection(
        controller: c,
        onOpenPin: _openPin,
        onAdd: () => showPinEditor(context, c,
            availableFeatures: widget.availableFeatures),
        onManage: () => showPinManager(context, c),
      );
      if (!wide) {
        // One column leads with what needs a decision, as the web Home does:
        // Today's Focus (approvals waiting first) sits right under the ask
        // box, and the shortcut tiles come after it, never above the fold.
        return AnumPage(onRefresh: c.load, children: [
          greeting,
          ask,
          focus,
          ...widget.sections,
          activity,
          commands,
          shortcuts,
          agentReady,
          pinned,
        ]);
      }
      final left = <Widget>[ask, commands, shortcuts, agentReady];
      final right = <Widget>[focus, ...widget.sections, activity, pinned];
      Widget column(List<Widget> items) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (var i = 0; i < items.length; i++) ...[
                items[i],
                if (i < items.length - 1)
                  const SizedBox(height: AnumSpacing.md),
              ]
            ],
          );
      return AnumPage(onRefresh: c.load, children: [
        greeting,
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(flex: 11, child: column(left)),
          const SizedBox(width: AnumSpacing.lg),
          Expanded(flex: 10, child: column(right)),
        ]),
      ]);
    });
  }

  Widget _shortcuts(AnumLocalizations t) {
    final counts = c.summary?.counts ?? const {};
    String? caption(String key) => switch (key) {
          'tasks' when (counts['tasks'] ?? 0) > 0 =>
            t.f('homeOpenCount', {'n': counts['tasks']!}),
          'automations' when (counts['automations'] ?? 0) > 0 =>
            t.f('homeFailedCount', {'n': counts['automations']!}),
          _ => null,
        };
    final tiles = [
      for (final f in homeShortcuts)
        if (f.key == 'command' || _can(f.key))
          HomeTile(
            icon: f.icon,
            label: t.t(f.labelKey),
            caption: caption(f.key),
            tone: f.tone,
            highlighted: f.key == 'command',
            onTap: () => f.key == 'command'
                ? widget.onCommand(null)
                : _open(HomeTarget(f.key)),
          ),
    ];
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      HomeSectionTitle(t.t('homeShortcuts')),
      const SizedBox(height: AnumSpacing.sm),
      HomeGrid(
        columnsFor: (w) => w < 300
            ? 2
            : w < 640
                ? 3
                : 4,
        children: tiles,
      ),
    ]);
  }
}

// -- greeting -------------------------------------------------------------------

class _Greeting extends StatelessWidget {
  const _Greeting(
      {required this.controller, required this.now, required this.onInsights});
  final HomeController controller;
  final DateTime now;
  final VoidCallback onInsights;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final name = controller.summary?.displayName;
    final hello = t.t(now.hour < 12
        ? 'homeGreetingMorning'
        : now.hour < 18
            ? 'homeGreetingAfternoon'
            : 'homeGreetingEvening');
    final date = MaterialLocalizations.of(context).formatFullDate(now);
    final open = controller.openInsights;
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(hello,
              style: TextStyle(color: p.muted, fontSize: AnumType.base)),
          const SizedBox(height: 2),
          if (name != null)
            Semantics(
              header: true,
              child: Text('$name 👋',
                  style: TextStyle(
                      color: p.text,
                      fontSize: AnumType.x4lPlus,
                      fontWeight: FontWeight.w800,
                      height: 1.15)),
            )
          else if (controller.summary != null)
            TextButton.icon(
              style: TextButton.styleFrom(padding: EdgeInsets.zero),
              onPressed: () => showNameDialog(context, controller),
              icon: const Icon(Icons.edit_outlined, size: 18),
              label: Text(t.t('homeAddName')),
            ),
          Text(date,
              style: TextStyle(color: p.faint, fontSize: AnumType.xsPlus)),
        ]),
      ),
      if (name != null)
        IconButton(
          tooltip: t.t('homeEditName'),
          onPressed: () => showNameDialog(context, controller),
          icon: AnumAvatar(name: name, size: 36),
        ),
      IconButton(
        tooltip: open == 0
            ? t.t('homeInsightsButton')
            : t.f('homeInsightsCount', {'n': open}),
        onPressed: onInsights,
        icon: Badge(
          isLabelVisible: open > 0,
          label: Text('$open'),
          child: Icon(Icons.auto_awesome_outlined, color: p.violet),
        ),
      ),
    ]);
  }
}

/// Under the shell's header: how many things ANUM noticed, opening ANUM
/// Proactive (the greeting row carries this button when Home draws its own).
class _InsightsEntry extends StatelessWidget {
  const _InsightsEntry({required this.controller, required this.onOpen});
  final HomeController controller;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final open = controller.openInsights;
    return Align(
      alignment: AlignmentDirectional.centerStart,
      child: TextButton.icon(
        onPressed: onOpen,
        icon: Badge(
          isLabelVisible: open > 0,
          label: Text('$open'),
          child: const Icon(Icons.auto_awesome_outlined),
        ),
        label: Text(open == 0
            ? t.t('homeInsightsButton')
            : t.f('homeInsightsCount', {'n': open})),
      ),
    );
  }
}

// -- ask ----------------------------------------------------------------------------

class _AskSection extends StatelessWidget {
  const _AskSection(
      {required this.ask, required this.onSubmit, required this.onVoice});
  final TextEditingController ask;
  final VoidCallback onSubmit;
  final VoidCallback onVoice;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Semantics(
        header: true,
        child: Text(t.t('homeHelpTitle'),
            style: TextStyle(
                color: p.text,
                fontSize: AnumType.x4l,
                fontWeight: FontWeight.w700,
                height: 1.2)),
      ),
      const SizedBox(height: AnumSpacing.sm),
      Row(children: [
        Expanded(
          child: TextField(
            controller: ask,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => onSubmit(),
            minLines: 1,
            maxLines: 3,
            decoration: InputDecoration(
              hintText: t.t('homeAsk'),
              prefixIcon: const Icon(Icons.search),
              border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(28),
                  borderSide: BorderSide(color: p.line)),
              enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(28),
                  borderSide: BorderSide(color: p.line)),
              suffixIcon: ListenableBuilder(
                listenable: ask,
                builder: (context, _) => ask.text.trim().isEmpty
                    ? const SizedBox.shrink()
                    : IconButton(
                        tooltip: t.t('homeAskSend'),
                        icon: const Icon(Icons.send),
                        onPressed: onSubmit,
                      ),
              ),
            ),
          ),
        ),
        const SizedBox(width: AnumSpacing.xs),
        Tooltip(
          message: t.t('homeSpeak'),
          child: Semantics(
            button: true,
            label: t.t('homeSpeak'),
            excludeSemantics: true,
            child: InkWell(
              customBorder: const CircleBorder(),
              onTap: onVoice,
              child: Ink(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: p.accentGradient,
                  boxShadow: [
                    BoxShadow(
                        color: p.violet.withValues(alpha: 0.45), blurRadius: 16)
                  ],
                ),
                child: const Icon(Icons.mic, color: Colors.white),
              ),
            ),
          ),
        ),
      ]),
    ]);
  }
}

// -- quick commands -------------------------------------------------------------------

class _CommandSection extends StatelessWidget {
  const _CommandSection({required this.controller, required this.onCommand});
  final HomeController controller;
  final ValueChanged<String?> onCommand;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final items = controller.modeCommands;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Row(children: [
        Expanded(
          child: Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                for (final m in CommandMode.values)
                  ChoiceChip(
                    avatar: Icon(
                        switch (m) {
                          CommandMode.work => Icons.work_outline,
                          CommandMode.personal => Icons.person_outline,
                          CommandMode.custom => Icons.tune,
                        },
                        size: 16),
                    label: Text(modeLabel(t, m)),
                    selected: controller.mode == m,
                    onSelected: (_) => controller.selectMode(m),
                  ),
              ]),
        ),
        if (controller.canEditCommands)
          TextButton(
            onPressed: () => showCommandManager(context, controller),
            child: Text(t.t('homePinManage')),
          ),
      ]),
      const SizedBox(height: AnumSpacing.xs),
      if (controller.commandsError != null)
        Text(controller.commandsError!,
            style: TextStyle(color: p.stop, fontSize: AnumType.sm))
      else
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          for (final command in items)
            _CommandChip(
              icon: Icons.bolt,
              label: commandLabel(t, command),
              onPressed: () => onCommand(commandPrompt(t, command)),
            ),
          if (controller.canEditCommands)
            _CommandChip(
              icon: Icons.add,
              label: t.t('homeAddCommand'),
              onPressed: () => showCommandEditor(context, controller),
            ),
          if (items.isEmpty && !controller.canEditCommands)
            Text(
                controller.commands.isEmpty
                    ? t.t('homeCommandsViewer')
                    : t.t('homeNoCommands'),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        ]),
    ]);
  }
}

/// A quick command as a pill button whose label wraps at large text sizes
/// (a Material chip would clip it).
class _CommandChip extends StatelessWidget {
  const _CommandChip(
      {required this.icon, required this.label, required this.onPressed});
  final IconData icon;
  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return OutlinedButton.icon(
      style: OutlinedButton.styleFrom(
        backgroundColor: p.surface2,
        shape: const StadiumBorder(),
        padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.sm, vertical: AnumSpacing.xs),
      ),
      onPressed: onPressed,
      icon: Icon(icon, size: 16, color: p.sky),
      label: Text(label),
    );
  }
}

// -- agent ready ---------------------------------------------------------------------------

class _AgentReady extends StatelessWidget {
  const _AgentReady({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    return AnumSurface(
      onTap: onTap,
      accent: p.violet,
      semanticLabel: '${t.t('homeAgentReady')}. ${t.t('homeAgentReadyText')}',
      child: Row(children: [
        const ExcludeSemantics(child: AnumOrb(size: 52)),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(t.t('homeAgentReady'),
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.base,
                    fontWeight: FontWeight.w700)),
            const SizedBox(height: 2),
            Text(t.t('homeAgentReadyText'),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          ]),
        ),
        Icon(Icons.arrow_forward, color: p.sky),
      ]),
    );
  }
}

// -- today's focus -----------------------------------------------------------------------------

class _FocusSection extends StatelessWidget {
  const _FocusSection({
    required this.controller,
    required this.onOpen,
    required this.onCommand,
    required this.canOpenTasks,
    required this.now,
  });
  final HomeController controller;
  final ValueChanged<HomeTarget> onOpen;
  final VoidCallback onCommand;
  final bool canOpenTasks;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final summary = controller.summary;
    final progress = summary?.progress;
    final failed = summary?.failedSources ?? const [];
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        HomeSectionTitle(
          t.t('homeFocus'),
          subtitle: summary == null || summary.highPriority == 0
              ? null
              : t.f('homeFocusHigh', {'n': summary.highPriority}),
          trailing: canOpenTasks
              ? TextButton(
                  onPressed: () => onOpen(const HomeTarget('tasks')),
                  child: Text(t.t('homeViewAll')))
              : null,
        ),
        if (summary == null && controller.phase == HomePhase.loading) ...[
          const SizedBox(height: AnumSpacing.md),
          const LinearProgressIndicator(),
          const SizedBox(height: AnumSpacing.xs),
          Text(t.t('homeLoading'), style: TextStyle(color: p.muted)),
        ] else if (summary == null && controller.phase == HomePhase.error)
          AnumEmpty(
            icon: Icons.cloud_off_outlined,
            title: t.t('homeErrorTitle'),
            message: t.f('homeErrorHint', {'message': controller.error ?? ''}),
            action: t.t('retry'),
            onAction: controller.load,
          )
        else if (summary != null) ...[
          if (progress != null && progress.total > 0) ...[
            const SizedBox(height: AnumSpacing.sm),
            Semantics(
              label: t.f('homeProgress',
                  {'done': progress.completed, 'total': progress.total}),
              excludeSemantics: true,
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(
                          value: progress.fraction, minHeight: 6),
                    ),
                    const SizedBox(height: AnumSpacing.xxs),
                    Text(
                        t.f('homeProgress', {
                          'done': progress.completed,
                          'total': progress.total
                        }),
                        style: TextStyle(
                            color: p.muted, fontSize: AnumType.xsPlus)),
                  ]),
            ),
          ],
          if (failed.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xs),
            Row(children: [
              Icon(Icons.warning_amber_rounded, size: 18, color: p.warn),
              const SizedBox(width: AnumSpacing.xs),
              Expanded(
                child: Text(
                    t.f('homePartial', {
                      'names': failed
                          .map((s) => t.t('homeSource_${s.name}') ==
                                  'homeSource_${s.name}'
                              ? s.name
                              : t.t('homeSource_${s.name}'))
                          .join(', ')
                    }),
                    style:
                        TextStyle(color: p.muted, fontSize: AnumType.xsPlus)),
              ),
            ]),
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: TextButton(
                  onPressed: controller.load, child: Text(t.t('retry'))),
            ),
          ],
          if (summary.focus.isEmpty)
            AnumEmpty(
              icon: Icons.check_circle_outline,
              title: t.t('homeFocusNone'),
              message: t.t('homeFocusNoneText'),
              action: t.t('homeAskSend'),
              onAction: onCommand,
            )
          else
            for (final item in summary.focus)
              _FocusRow(item: item, onTap: () => onOpen(item.target)),
        ],
      ]),
    );
  }
}

class _FocusRow extends StatelessWidget {
  const _FocusRow({required this.item, required this.onTap});
  final FocusItem item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final title = focusTitle(t, item);
    final detail = focusDetail(t, item);
    final tone = severityTone(item.severity);
    return Semantics(
      button: true,
      label: [title, if (detail != null) detail].join(', '),
      excludeSemantics: true,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
          child: Row(children: [
            HomeIconSquare(icon: focusIcon(item), tone: tone, size: 40),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                            color: p.text,
                            fontSize: AnumType.mdPlus,
                            fontWeight: FontWeight.w600)),
                    if (detail != null)
                      Text(detail,
                          style: TextStyle(
                              color: p.muted, fontSize: AnumType.xsPlus)),
                    if (item.progress != null) ...[
                      const SizedBox(height: AnumSpacing.xxs),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(4),
                        child: LinearProgressIndicator(
                            value: item.progress, minHeight: 4),
                      ),
                    ],
                  ]),
            ),
            if (item.count != null) ...[
              const SizedBox(width: AnumSpacing.xs),
              AnumPill(label: '${item.count}', tone: tone),
            ],
            Icon(Icons.chevron_right, color: p.faint),
          ]),
        ),
      ),
    );
  }
}

// -- recent activity -----------------------------------------------------------------------------

class _ActivitySection extends StatelessWidget {
  const _ActivitySection({
    required this.controller,
    required this.onOpen,
    required this.onCommand,
    required this.canOpenTasks,
    required this.now,
  });
  final HomeController controller;
  final ValueChanged<HomeTarget> onOpen;
  final VoidCallback onCommand;
  final bool canOpenTasks;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final summary = controller.summary;
    if (summary == null) return const SizedBox.shrink();
    final items = summary.activity.take(5).toList();
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        HomeSectionTitle(
          t.t('homeActivity'),
          trailing: canOpenTasks && items.isNotEmpty
              ? TextButton(
                  onPressed: () => onOpen(const HomeTarget('tasks')),
                  child: Text(t.t('homeViewAll')))
              : null,
        ),
        if (items.isEmpty)
          AnumEmpty(
            icon: Icons.history,
            title: t.t('homeActivityNone'),
            message: t.t('homeActivityNoneText'),
            action: t.t('homeAskSend'),
            onAction: onCommand,
          )
        else
          for (final item in items)
            AnumRow(
              icon: item.kind == 'automation_run'
                  ? Icons.auto_mode_outlined
                  : switch (item.status) {
                      'completed' => Icons.check_circle_outline,
                      'failed' => Icons.error_outline,
                      'waiting_approval' => Icons.front_hand_outlined,
                      _ => Icons.bolt_outlined,
                    },
              tone: switch (item.status) {
                'completed' => AnumTone.ok,
                'failed' => AnumTone.stop,
                'waiting_approval' || 'paused' => AnumTone.warn,
                _ => AnumTone.info,
              },
              title: item.title,
              subtitle: item.kind == 'automation_run'
                  ? t.f('homeActivityAutomation',
                      {'status': statusLabel(t, item.status)})
                  : statusLabel(t, item.status),
              detail: agoLabel(t, item.at, now),
              trailing: Icon(Icons.chevron_right, color: context.palette.faint),
              onTap: () => onOpen(item.target),
            ),
      ]),
    );
  }
}

// -- pinned ------------------------------------------------------------------------------------------

class _PinnedSection extends StatelessWidget {
  const _PinnedSection({
    required this.controller,
    required this.onOpenPin,
    required this.onAdd,
    required this.onManage,
  });
  final HomeController controller;
  final ValueChanged<HomePin> onOpenPin;
  final VoidCallback onAdd;
  final VoidCallback onManage;

  @override
  Widget build(BuildContext context) {
    final t = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final pins = controller.pins;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      HomeSectionTitle(
        t.t('homePinned'),
        subtitle: pins.isEmpty ? t.t('homePinNone') : null,
        trailing: pins.isEmpty
            ? null
            : TextButton(
                onPressed: onManage, child: Text(t.t('homePinManage'))),
      ),
      const SizedBox(height: AnumSpacing.sm),
      if (controller.pinsError != null)
        Row(children: [
          Expanded(
              child: Text(controller.pinsError!,
                  style: TextStyle(color: p.stop, fontSize: AnumType.sm))),
        ]),
      if (controller.pinsError != null)
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child:
              TextButton(onPressed: controller.load, child: Text(t.t('retry'))),
        )
      else
        HomeGrid(
          columnsFor: (w) => w < 300
              ? 2
              : w < 640
                  ? 3
                  : 4,
          children: [
            for (final pin in pins)
              HomeTile(
                icon: pinIcon(pin),
                label: pin.label,
                tone: switch (pin.kind) {
                  PinKind.workspace => AnumTone.accent,
                  PinKind.link => AnumTone.ok,
                  PinKind.feature => AnumTone.info,
                },
                onTap: () => onOpenPin(pin),
              ),
            HomeTile(
              icon: Icons.add,
              label: t.t('homePinAdd'),
              tone: AnumTone.neutral,
              onTap: onAdd,
            ),
          ],
        ),
    ]);
  }
}
