import 'package:flutter/material.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import '../chat/chat_controller.dart';
import '../chat/chat_screen.dart';
import '../voice/voice_controller.dart';
import '../voice/voice_screen.dart';
import '../settings/settings_controller.dart';
import '../settings/settings_screen.dart';
import '../governance/governance_controller.dart';
import '../governance/governance_screen.dart';
import '../developer/developer_assist.dart';
import '../developer/developer_controller.dart';
import '../developer/developer_screen.dart';
import '../whatsapp/whatsapp_controller.dart';
import '../whatsapp/whatsapp_screen.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../automations/automation_repository.dart';
import '../automations/automations_controller.dart';
import 'api_workspace_repository.dart';
import 'approvals_screen.dart';
import 'automations_screen.dart';
import 'destinations.dart';
import 'home_screen.dart';
import 'resources_screen.dart';
import 'tasks_screen.dart';

class WorkspaceHome extends StatefulWidget {
  const WorkspaceHome(
      {super.key,
      required this.controller,
      required this.chatController,
      required this.voiceController,
      required this.settingsController,
      required this.governanceController,
      required this.localeController,
      this.developerController,
      this.automationsController,
      this.chatDeveloper,
      this.whatsappController,
      this.workspaceName,
      this.initialIndex = 0});
  final WorkspaceController controller;
  final ChatController chatController;
  final VoiceController voiceController;
  final SettingsController settingsController;
  final GovernanceController governanceController;
  final LocaleController localeController;

  /// Developer mode (owners only). Without it the destination is absent.
  final DeveloperController? developerController;

  /// Automations (search, paging, schedules, runs). Built from the workspace
  /// repository's API client when not given.
  final AutomationsController? automationsController;

  /// "build ..." in the Assistant; the voice one lives on the voice controller.
  final DeveloperAssist? chatDeveloper;

  /// The WhatsApp inbox (docs/whatsapp.md), opened as its own page from the
  /// app bar (wide layouts) or the More sheet (phones).
  final WhatsAppController? whatsappController;
  final String? workspaceName;
  final int initialIndex;
  @override
  State<WorkspaceHome> createState() => _WorkspaceHomeState();
}

class _WorkspaceHomeState extends State<WorkspaceHome> with RestorationMixin {
  late final AutomationsController _automations =
      widget.automationsController ?? _ownAutomations();
  bool _ownsAutomations = false;

  AutomationsController _ownAutomations() {
    _ownsAutomations = true;
    final repository = widget.controller.repository;
    return AutomationsController(repository is ApiWorkspaceRepository
        ? ApiAutomationRepository(repository.api)
        : const UnavailableAutomationRepository());
  }

  late final RestorableInt _index;
  int get index => _index.value;
  @override
  String? get restorationId => 'workspace_home';
  @override
  void restoreState(RestorationBucket? oldBucket, bool initialRestore) =>
      registerForRestoration(_index, 'selected_destination');
  @override
  void initState() {
    super.initState();
    _index = RestorableInt(widget.initialIndex);
    if (widget.controller.phase == LoadPhase.initial) widget.controller.load();
    // Whether to show Developer: the caller's role, checked once per workspace.
    final developer = widget.developerController;
    if (developer != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) developer.ensureOwner();
      });
    }
  }

  /// Developer is in the navigation only for owners.
  bool get _showDeveloper => widget.developerController?.owner ?? false;

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<bool>(
      valueListenable: widget.chatController.listOpen,
      builder: (context, listOpen, shell) {
        // On a phone the Assistant's conversation list sits in place of the
        // chat: Back closes it instead of leaving the workspace.
        final backClosesList =
            listOpen && index == WorkspaceDestination.assistant;
        return PopScope<Object?>(
            canPop: !backClosesList,
            onPopInvokedWithResult: (didPop, result) {
              if (!didPop && backClosesList) {
                widget.chatController.closeList();
                return;
              }
              // Leaving the workspace never keeps the microphone open.
              widget.voiceController.setVisible(false);
            },
            child: shell!);
      },
      child: _buildShell(context));

  void _openWhatsApp(BuildContext context) => Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) =>
              WhatsAppScreen(controller: widget.whatsappController!)));

  void _openSettings(BuildContext context) => Navigator.push(
      context,
      MaterialPageRoute<void>(
          builder: (_) =>
              SettingsScreen(controller: widget.settingsController)));

  Widget _buildShell(BuildContext context) => ListenableBuilder(
        listenable:
            Listenable.merge([widget.controller, widget.developerController]),
        builder: (context, _) {
          final developer = widget.developerController;
          // Chat and voice offer "Open Developer" only where it is listed.
          final openDeveloper = _showDeveloper
              ? () => _select(WorkspaceDestination.developer)
              : null;
          final pages = [
            AssistantScreen(
              controller: widget.chatController,
              active: index == WorkspaceDestination.assistant,
              onOpenSettings: () => _openSettings(context),
              developer: widget.chatDeveloper,
              onOpenDeveloper: openDeveloper,
            ),
            HomeScreen(
              controller: widget.controller,
              workspaceName: widget.workspaceName,
              onNavigate: _select,
              onOpenTask: (task) =>
                  openTaskDetail(context, widget.controller, task),
            ),
            TasksScreen(controller: widget.controller),
            VoiceScreen(
                controller: widget.voiceController,
                onOpenApprovals: () => _select(WorkspaceDestination.approvals),
                onOpenDeveloper: openDeveloper),
            ApprovalsScreen(controller: widget.controller),
            AutomationsScreen(automations: _automations),
            ResourcesScreen(controller: widget.controller),
            if (developer != null)
              DeveloperScreen(
                controller: developer,
                active: index == WorkspaceDestination.developer,
                onOpenSettings: () => _openSettings(context),
              )
            else
              const SizedBox.shrink(),
          ];
          final l = context.anum;
          final assistant = NavigationDestination(
              icon: const Icon(Icons.chat_bubble_outline),
              selectedIcon: const Icon(Icons.chat_bubble),
              label: l.t('assistant'));
          final destinations = [
            assistant,
            NavigationDestination(
                icon: const Icon(Icons.home_outlined),
                selectedIcon: const Icon(Icons.home),
                label: l.t('home')),
            NavigationDestination(
                icon: const Icon(Icons.task_alt_outlined),
                selectedIcon: const Icon(Icons.task_alt),
                label: l.t('tasks')),
            NavigationDestination(
                icon: const Icon(Icons.mic_none),
                selectedIcon: const Icon(Icons.mic),
                label: l.t('voice')),
            NavigationDestination(
                icon: Badge(
                    isLabelVisible:
                        widget.controller.pendingApprovals.isNotEmpty,
                    label: Text('${widget.controller.pendingApprovals.length}'),
                    child: const Icon(Icons.approval_outlined)),
                selectedIcon: const Icon(Icons.approval),
                label: l.t('approvals')),
            NavigationDestination(
                icon: const Icon(Icons.account_tree_outlined),
                selectedIcon: const Icon(Icons.account_tree),
                label: l.t('automation')),
            NavigationDestination(
                icon: const Icon(Icons.folder_outlined),
                selectedIcon: const Icon(Icons.folder),
                label: l.t('resources')),
            if (_showDeveloper)
              NavigationDestination(
                  icon: const Icon(Icons.construction_outlined),
                  selectedIcon: const Icon(Icons.construction),
                  label: l.t('developer')),
          ];
          final compactDestinations = [
            assistant,
            NavigationDestination(
                icon: const Icon(Icons.home_outlined),
                selectedIcon: const Icon(Icons.home),
                label: l.t('home')),
            NavigationDestination(
                icon: const Icon(Icons.task_alt_outlined),
                selectedIcon: const Icon(Icons.task_alt),
                label: l.t('tasks')),
            NavigationDestination(
                icon: const Icon(Icons.folder_outlined),
                selectedIcon: const Icon(Icons.folder),
                label: l.t('resources')),
            const NavigationDestination(
                icon: Icon(Icons.more_horiz), label: 'More')
          ];
          final content = Column(children: [
            if (widget.controller.phase == LoadPhase.offline)
              _StateBanner(icon: Icons.cloud_off, text: l.t('offline')),
            if (widget.controller.message != null &&
                widget.controller.phase == LoadPhase.error)
              _StateBanner(
                  icon: Icons.error_outline, text: widget.controller.message!),
            Expanded(
                child: widget.controller.phase == LoadPhase.loading &&
                        widget.controller.snapshot == null
                    ? const Center(child: CircularProgressIndicator())
                    : IndexedStack(index: index, children: pages))
          ]);
          return LayoutBuilder(builder: (context, constraints) {
            final expanded = constraints.maxWidth >= 840;
            return Scaffold(
              appBar: expanded
                  ? AppBar(
                      title: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(l.t('workspace')),
                            Text(l.t('subtitle'),
                                style: const TextStyle(
                                    fontSize: 12,
                                    fontWeight: FontWeight.normal))
                          ]),
                      actions: [
                          PopupMenuButton<String>(
                              tooltip: l.t('language'),
                              onSelected: widget.localeController.select,
                              itemBuilder: (_) => [
                                    PopupMenuItem(
                                        value: 'en',
                                        child: Text(l.t('english'))),
                                    PopupMenuItem(
                                        value: 'ar', child: Text(l.t('arabic')))
                                  ],
                              icon: const Icon(Icons.language)),
                          if (widget.whatsappController != null)
                            IconButton(
                                onPressed: () => _openWhatsApp(context),
                                tooltip: l.t('whatsapp'),
                                icon: const Icon(Icons.forum_outlined)),
                          IconButton(
                              onPressed: () => Navigator.push(
                                  context,
                                  MaterialPageRoute<void>(
                                      builder: (_) => GovernanceScreen(
                                          controller:
                                              widget.governanceController))),
                              tooltip: l.t('organization'),
                              icon: const Icon(
                                  Icons.admin_panel_settings_outlined)),
                          IconButton(
                              onPressed: () => Navigator.push(
                                  context,
                                  MaterialPageRoute<void>(
                                      builder: (_) => SettingsScreen(
                                          controller:
                                              widget.settingsController))),
                              tooltip: l.t('settings'),
                              icon: const Icon(Icons.settings_outlined)),
                          IconButton(
                              onPressed: widget.controller.mutating
                                  ? null
                                  : widget.controller.load,
                              tooltip: l.t('refresh'),
                              icon: const Icon(Icons.refresh))
                        ])
                  : null,
              body: expanded
                  ? Row(children: [
                      NavigationRail(
                          // A deep link to Developer for a non-owner selects
                          // nothing: the destination is not listed for them.
                          selectedIndex:
                              index < destinations.length ? index : null,
                          onDestinationSelected: _select,
                          labelType: NavigationRailLabelType.all,
                          destinations: destinations
                              .map((item) => NavigationRailDestination(
                                  icon: item.icon,
                                  selectedIcon: item.selectedIcon,
                                  label: Text(item.label)))
                              .toList()),
                      const VerticalDivider(width: 1),
                      Expanded(child: content)
                    ])
                  : content,
              bottomNavigationBar: expanded
                  ? null
                  : MediaQuery.withClampedTextScaling(
                      maxScaleFactor: 1.3,
                      child: Builder(
                        builder: (context) => NavigationBar(
                            // Labels that cannot fit their slot (a narrow phone at
                            // large text) are hidden rather than wrapped and
                            // clipped; each destination keeps its tooltip and
                            // spoken label.
                            labelBehavior: navigationLabelsFit(context,
                                    compactDestinations, constraints.maxWidth)
                                ? NavigationDestinationLabelBehavior.alwaysShow
                                : NavigationDestinationLabelBehavior.alwaysHide,
                            selectedIndex: switch (index) {
                              WorkspaceDestination.assistant => 0,
                              WorkspaceDestination.home => 1,
                              WorkspaceDestination.tasks => 2,
                              WorkspaceDestination.resources => 3,
                              _ => 4,
                            },
                            onDestinationSelected: (value) {
                              switch (value) {
                                case 0:
                                  _select(WorkspaceDestination.assistant);
                                case 1:
                                  _select(WorkspaceDestination.home);
                                case 2:
                                  _select(WorkspaceDestination.tasks);
                                case 3:
                                  _select(WorkspaceDestination.resources);
                                default:
                                  _showMore(context);
                              }
                            },
                            destinations: compactDestinations),
                      ),
                    ),
            );
          });
        },
      );

  void _select(int value) {
    setState(() => _index.value = value);
    // After a workspace switch (Settings) the role is checked again, so
    // Developer appears or disappears with it.
    widget.developerController?.ensureOwner();
  }

  Future<void> _showMore(BuildContext context) async {
    final value = await showModalBottomSheet<int>(
        context: context,
        showDragHandle: true,
        builder: (context) => SafeArea(
                child: SingleChildScrollView(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
              if (widget.whatsappController != null)
                ListTile(
                    leading: const Icon(Icons.forum_outlined),
                    title: Text(context.anum.t('whatsapp')),
                    onTap: () {
                      Navigator.pop(context);
                      _openWhatsApp(context);
                    }),
              ListTile(
                  leading: const Icon(Icons.mic),
                  title: const Text('Voice'),
                  onTap: () =>
                      Navigator.pop(context, WorkspaceDestination.voice)),
              ListTile(
                  leading: const Icon(Icons.approval_outlined),
                  title: const Text('Approvals'),
                  onTap: () =>
                      Navigator.pop(context, WorkspaceDestination.approvals)),
              ListTile(
                  leading: const Icon(Icons.account_tree_outlined),
                  title: const Text('Automations'),
                  onTap: () =>
                      Navigator.pop(context, WorkspaceDestination.automations)),
              if (_showDeveloper)
                ListTile(
                    leading: const Icon(Icons.construction_outlined),
                    title: Text(context.anum.t('developer')),
                    onTap: () =>
                        Navigator.pop(context, WorkspaceDestination.developer)),
              ListTile(
                  leading: const Icon(Icons.policy_outlined),
                  title: const Text('Policy packs'),
                  onTap: () {
                    Navigator.pop(context);
                    Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                            builder: (_) => GovernanceScreen(
                                controller: widget.governanceController,
                                initialSection: GovernanceSection.policies)));
                  }),
              ListTile(
                  leading: const Icon(Icons.storefront_outlined),
                  title: const Text('Marketplace'),
                  onTap: () {
                    Navigator.pop(context);
                    Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                            builder: (_) => GovernanceScreen(
                                controller: widget.governanceController,
                                initialSection:
                                    GovernanceSection.marketplace)));
                  }),
              ListTile(
                  leading: const Icon(Icons.route),
                  title: const Text('Advanced routing'),
                  onTap: () {
                    Navigator.pop(context);
                    Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                            builder: (_) => GovernanceScreen(
                                controller: widget.governanceController,
                                initialSection: GovernanceSection.routing)));
                  }),
              ListTile(
                  leading: const Icon(Icons.settings_outlined),
                  title: const Text('Settings'),
                  onTap: () {
                    Navigator.pop(context);
                    Navigator.push(
                        context,
                        MaterialPageRoute<void>(
                            builder: (_) => SettingsScreen(
                                controller: widget.settingsController)));
                  })
            ]))));
    if (value != null) _select(value);
  }

  @override
  void dispose() {
    _index.dispose();
    if (_ownsAutomations) _automations.dispose();
    super.dispose();
  }
}

/// Whether every label fits one line in its share of a bottom navigation
/// bar [width] wide, at the current text scale.
bool navigationLabelsFit(BuildContext context,
    List<NavigationDestination> destinations, double width) {
  final theme = Theme.of(context);
  final style = (theme.textTheme.labelMedium ?? const TextStyle()).merge(theme
      .navigationBarTheme.labelTextStyle
      ?.resolve(const {WidgetState.selected}));
  final slot = width / destinations.length - AnumSpacing.xs;
  for (final destination in destinations) {
    final painter = TextPainter(
      text: TextSpan(text: destination.label, style: style),
      textDirection: Directionality.of(context),
      textScaler: MediaQuery.textScalerOf(context),
      maxLines: 1,
    )..layout();
    final fits = painter.width <= slot;
    painter.dispose();
    if (!fits) return false;
  }
  return true;
}

class _StateBanner extends StatelessWidget {
  const _StateBanner({required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) => Material(
      color: Theme.of(context).colorScheme.errorContainer,
      child: SafeArea(
          bottom: false,
          child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Row(children: [
                Icon(icon, size: 18),
                const SizedBox(width: 10),
                Expanded(
                    child: Text(text,
                        style: Theme.of(context).textTheme.bodySmall))
              ]))));
}
