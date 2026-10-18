import '../integrations/integrations_controller.dart';
import '../integrations/integrations_repository.dart';
import '../integrations/integrations_screen.dart';
import 'package:flutter/material.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import '../chat/chat_cards.dart';
import '../chat/chat_controller.dart';
import '../chat/chat_screen.dart';
import '../voice/voice_controller.dart';
import '../voice/voice_screen.dart';
import '../settings/account_identity.dart';
import '../settings/profile_screen.dart';
import '../settings/settings_controller.dart';
import '../settings/settings_screen.dart';
import '../team/team_screens.dart';
import '../team/team_strings.dart';
import '../governance/governance_controller.dart';
import '../governance/governance_screen.dart';
import '../agents/agents_controller.dart';
import '../agents/agents_screen.dart';
import '../developer/developer_assist.dart';
import '../developer/developer_controller.dart';
import '../developer/developer_screen.dart';
import '../whatsapp/whatsapp_controller.dart';
import '../whatsapp/whatsapp_screen.dart';
import '../email/email_controller.dart';
import '../email/inbox_screen.dart';
import '../commerce/commerce_controller.dart';
import '../../data/api_client.dart';
import '../commerce/commerce_screen.dart';
import '../finance/finance_controller.dart';
import '../finance/finance_screen.dart';
import '../library/library_models.dart' show Favorite;
import '../library/library_repository.dart';
import '../library/library_screens.dart';
import '../marketing/marketing_controller.dart';
import '../marketing/marketing_screen.dart';
import '../admin/owned_controller.dart';
import '../autonomy/autonomy_controller.dart';
import '../autonomy/autonomy_screens.dart';
import '../home/home_controller.dart';
import '../home/home_models.dart';
import '../home/home_repository.dart';
import '../home/home_screen.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/theme/theme_controller.dart';
import '../../src/widgets/brand.dart';
import '../../src/widgets/depth.dart';
import '../../src/widgets/greeting_header.dart';
import '../../src/widgets/navigation.dart';
import '../automations/automation_repository.dart';
import '../automations/automations_controller.dart';
import 'api_workspace_repository.dart';
import 'approvals_screen.dart';
import 'automations_screen.dart';
import 'destinations.dart';
import 'resources_screen.dart';
import 'tasks_screen.dart';

/// The signed-in app shell (ANUM 360): on phones a bottom bar with Home,
/// Chat, the orb (ANUM Command), Tasks and Menu; from 840 px a navigation
/// rail with the orb at its top. The Menu drawer reaches every other
/// destination. Pages keep their state in an [IndexedStack].
class WorkspaceHome extends StatefulWidget {
  const WorkspaceHome(
      {super.key,
      required this.controller,
      required this.chatController,
      required this.voiceController,
      required this.settingsController,
      required this.governanceController,
      required this.localeController,
      this.themeController,
      this.developerController,
      this.automationsController,
      this.marketingController,
      this.chatDeveloper,
      this.whatsappController,
      this.emailController,
      this.commerceController,
      this.financeController,
      this.homeController,
      this.onOpenCommand,
      this.agentsController,
      this.agentChat,
      this.agentCall,
      this.onCommandRequest,
      this.workspaceName,
      this.library,
      this.initialIndex = WorkspaceDestination.home});
  final WorkspaceController controller;
  final ChatController chatController;
  final VoiceController voiceController;
  final SettingsController settingsController;
  final GovernanceController governanceController;
  final LocaleController localeController;

  /// Light, dark or system (Profile and settings > Theme). Without one the
  /// choice applies to this shell's Profile screen only (tests).
  final ThemeController? themeController;

  /// Developer mode (owners only). Without it the destination is absent.
  final DeveloperController? developerController;

  /// Automations (search, paging, schedules, runs). Built from the workspace
  /// repository's API client when not given.
  final AutomationsController? automationsController;

  /// Social media marketing (docs/marketing.md), opened from the menu.
  final MarketingController? marketingController;

  /// "build ..." in the Assistant; the voice one lives on the voice controller.
  final DeveloperAssist? chatDeveloper;

  /// The WhatsApp inbox (docs/whatsapp.md), opened from the Menu.
  final WhatsAppController? whatsappController;

  /// The unified inbox (docs/email.md), opened from the Menu.
  final EmailController? emailController;

  /// Commerce (docs/commerce.md), opened from Home.
  final CommerceController? commerceController;

  /// Finance (docs/finance.md), opened from the menu and Home pins.
  final FinanceController? financeController;

  /// Home (docs/home.md). Built from the workspace repository's API client
  /// when not given.
  final HomeController? homeController;

  /// Opens ANUM Command with a typed request or a quick command (null: the
  /// empty command screen). Without it a request goes to the Assistant chat
  /// and an empty one opens the orb's surface.
  final ValueChanged<String?>? onCommandRequest;

  /// The orb: opens ANUM Command. Without it the orb opens the voice
  /// assistant, the command surface the app has today.
  final VoidCallback? onOpenCommand;

  /// User-built agents (docs/agents.md), opened from Home. Absent: no entry.
  final AgentsController? agentsController;
  final AgentChatBuilder? agentChat;
  final AgentCallOpener? agentCall;
  final String? workspaceName;

  /// Favorites, knowledge base and reports (docs/knowledge-base.md): the
  /// menu entries, the Home quick access and the chat's "View Full Report".
  final LibraryRepository? library;
  final int initialIndex;
  @override
  State<WorkspaceHome> createState() => _WorkspaceHomeState();
}

class _WorkspaceHomeState extends State<WorkspaceHome> with RestorationMixin {
  late final AutomationsController _automations =
      widget.automationsController ?? _ownAutomations();
  bool _ownsAutomations = false;
  late final HomeController _home = widget.homeController ?? _ownHome();
  bool _ownsHome = false;
  late final ThemeController _theme =
      widget.themeController ?? ThemeController(store: MemoryThemeStore());
  final _scaffold = GlobalKey<ScaffoldState>();

  /// Who is signed in, for the greeting and the avatar.
  AccountIdentity? _identity;

  AutomationsController _ownAutomations() {
    _ownsAutomations = true;
    final repository = widget.controller.repository;
    return AutomationsController(repository is ApiWorkspaceRepository
        ? ApiAutomationTriggerRepository(repository.api)
        : const UnavailableAutomationRepository());
  }

  /// Integrations (docs/mobile.md#anum-360-lists): its own screen on the
  /// integrations API; Resources > Services without the API repository.
  late final IntegrationsController? _integrations =
      widget.controller.repository is ApiWorkspaceRepository
          ? IntegrationsController(ApiIntegrationsRepository(
              (widget.controller.repository as ApiWorkspaceRepository).api))
          : null;
  HomeController _ownHome() {
    _ownsHome = true;
    final repository = widget.controller.repository;
    return HomeController(repository is ApiWorkspaceRepository
        ? ApiHomeRepository(repository.api)
        : const UnavailableHomeRepository());
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
    _loadIdentity();
    // Whether to show Developer: the caller's role, checked once per workspace.
    final developer = widget.developerController;
    if (developer != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) developer.ensureOwner();
      });
    }
  }

  Future<void> _loadIdentity() async {
    try {
      final session =
          await widget.settingsController.repository.restoreSession();
      if (!mounted || session == null) return;
      setState(() => _identity = AccountIdentity.fromSession(session));
    } on Object {
      // The header falls back to the workspace name.
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

  void _push(Widget page) =>
      Navigator.push(context, MaterialPageRoute<void>(builder: (_) => page));

  void _openWhatsApp() =>
      _push(WhatsAppScreen(controller: widget.whatsappController!));
  void _openInbox() => _push(InboxScreen(controller: widget.emailController!));
  void _openFinance() =>
      _push(FinanceScreen(controller: widget.financeController!));

  void _openMarketing() =>
      _push(MarketingScreen(controller: widget.marketingController!));
  void _openGovernance(GovernanceSection section) => _push(GovernanceScreen(
      controller: widget.governanceController, initialSection: section));

  /// Integrations (docs/mobile.md#anum-360-lists): its own screen on the
  /// integrations API, Resources > Services without it.
  void _openIntegrations() {
    final integrations = _integrations;
    if (integrations == null) {
      _openResources(ResourcesTab.services,
          AnumLocalizations.of(context).t('shellIntegrations'));
      return;
    }
    _push(Scaffold(
        appBar: AppBar(),
        body: IntegrationsScreen(
            controller: integrations,
            onOpenInbox: widget.emailController == null ? null : _openInbox)));
  }

  void _openResources(int tab, String title) => _push(Scaffold(
      appBar: AppBar(title: Text(title)),
      body: ResourcesScreen(controller: widget.controller, initialTab: tab)));
  void _openProfile() => _push(ProfileScreen(
      settings: widget.settingsController,
      locale: widget.localeController,
      theme: _theme,
      workspaceName: widget.workspaceName,
      onAskAnum: () => _select(WorkspaceDestination.assistant)));
  void _openSettings() =>
      _push(SettingsScreen(controller: widget.settingsController));
  void _openReport(String id) =>
      _push(ReportDetailScreen(repository: widget.library!, id: id));
  void _openFavorites() => _push(
      FavoritesScreen(repository: widget.library!, onOpenOther: _openFavorite));
  void _openReports() => _push(ReportsScreen(repository: widget.library!));
  // Knowledge Base: collections first; memories and files stay one tap away.
  void _openKnowledge() => _push(KnowledgeScreen(
      repository: widget.library!,
      onOpenMemories: () {
        Navigator.of(context).popUntil((route) => route.isFirst);
        _select(WorkspaceDestination.resources);
      }));

  /// A starred item that lives in another destination.
  void _openFavorite(Favorite favorite) {
    Navigator.of(context).popUntil((route) => route.isFirst);
    switch (favorite.itemType) {
      case 'conversation':
        _select(WorkspaceDestination.assistant);
        widget.chatController.open(favorite.itemId);
      case 'task':
        _select(WorkspaceDestination.tasks);
      case 'automation':
        _select(WorkspaceDestination.automations);
      default:
        _select(WorkspaceDestination.resources);
    }
  }

  void _openCommand() {
    final open = widget.onOpenCommand;
    if (open != null) {
      open();
    } else {
      _select(WorkspaceDestination.voice);
    }
  }

  void _commandRequest(String? text) {
    final request = widget.onCommandRequest;
    if (request != null) {
      request(text);
    } else if (text == null || text.trim().isEmpty) {
      _openCommand();
    } else {
      _select(WorkspaceDestination.assistant);
      widget.chatController.send(text);
    }
  }

  /// What Home may open from this shell; tiles and pins for anything else
  /// are hidden.
  Set<String> get _homeFeatures => {
        'command',
        'chat',
        'agents',
        'voice',
        'automations',
        'tasks',
        'documents',
        'integrations',
        'knowledge',
        'approvals',
        'settings',
        'insights',
        'more',
        'calendar',
        'tyres',
        if (widget.library != null) ...{'reports', 'favorites'},
        if (widget.settingsController.team != null) 'team',
        if (widget.emailController != null) 'email',
        if (widget.whatsappController != null) 'whatsapp',
        if (widget.settingsController.autonomy != null) 'autonomy',
        if (widget.commerceController != null) 'commerce',
        if (widget.financeController != null) 'finance',
        if (widget.marketingController != null) 'marketing',
      };

  void _openTarget(HomeTarget target) {
    final l = context.anum;
    switch (target.feature) {
      case 'chat':
        _select(WorkspaceDestination.assistant);
      case 'voice':
        _select(WorkspaceDestination.voice);
      case 'automations':
        _select(WorkspaceDestination.automations);
      case 'approvals':
        _select(WorkspaceDestination.approvals);
      case 'knowledge' when widget.library != null:
        _openKnowledge();
      case 'knowledge':
        _select(WorkspaceDestination.resources);
      case 'reports' when widget.library != null:
        if (target.id case final id?) {
          _openReport(id);
        } else {
          _openReports();
        }
      case 'favorites' when widget.library != null:
        _openFavorites();
      case 'calendar':
        _openResources(ResourcesTab.meetings, l.t('homeTileCalendar'));
      case 'tyres':
        Navigator.of(context).pushNamed('/tyres');
      case 'team' when widget.settingsController.team != null:
        _push(TeamHubScreen(repository: widget.settingsController.team!));
      case 'tasks':
        final id = target.id;
        final task = id == null
            ? null
            : widget.controller.tasks.where((t) => t.id == id).firstOrNull;
        if (task != null) {
          openTaskDetail(context, widget.controller, task);
        } else {
          _select(WorkspaceDestination.tasks);
        }
      case 'agents' when widget.agentsController != null:
        _openAgents();
      case 'agents':
        _openResources(ResourcesTab.skills, l.t('shellAgents'));
      case 'integrations':
        _openIntegrations();
      case 'documents':
        _openResources(ResourcesTab.files, l.t('homeTileDocuments'));
      case 'settings':
        _openProfile();
      case 'email' when widget.emailController != null:
        _openInbox();
      case 'whatsapp' when widget.whatsappController != null:
        _openWhatsApp();
      case 'autonomy' when widget.settingsController.autonomy != null:
        final repository = widget.settingsController.autonomy!;
        _push(OwnedController<ActivityController>(
            create: () => ActivityController(repository),
            builder: (_, c) => ActivityScreen(controller: c)));
      case 'commerce' when widget.commerceController != null:
        _push(CommerceScreen(controller: widget.commerceController!));
      case 'finance' when widget.financeController != null:
        _openFinance();
      case 'marketing' when widget.marketingController != null:
        _openMarketing();
      case 'more':
        _scaffold.currentState?.openDrawer();
      default:
        _openCommand();
    }
  }

  /// A pinned workspace: the API confirms the membership, then the shell
  /// reloads in it (as Settings > Switch workspace does).
  Future<void> _switchWorkspace(String workspaceId) async {
    final messenger = ScaffoldMessenger.maybeOf(context);
    final l = context.anum;
    try {
      await widget.settingsController.repository.switchWorkspace(workspaceId);
      await Future.wait([
        widget.settingsController.load(),
        widget.controller.load(),
        _home.load(),
      ]);
    } on Object catch (error) {
      messenger?.showSnackBar(SnackBar(
          content: Text(l.f('homeSwitchFailed', {
        'name': workspaceId,
        'message': error is ApiException ? error.readable : '$error'
      }))));
    }
  }

  /// The greeting header: avatar (Profile), name, date and the bell, whose
  /// count is the approvals waiting in this workspace.
  Widget _header({List<Widget>? trailing}) => AnumGreetingHeader(
        name: _identity?.displayName ??
            widget.workspaceName ??
            context.anum.t('workspace'),
        subtitle: widget.workspaceName,
        notificationCount: widget.controller.pendingApprovals.length,
        onNotifications: () => _select(WorkspaceDestination.approvals),
        onProfile: _openProfile,
        trailing: trailing,
      );

  /// Links on Assistant cards (docs/conversations.md#tools-in-chat).
  void _openChatTarget(ChatTarget target) => switch (target.kind) {
        ChatTargetKind.email ||
        ChatTargetKind.inbox =>
          widget.emailController == null ? null : _openInbox(),
        ChatTargetKind.whatsapp ||
        ChatTargetKind.whatsappInbox =>
          widget.whatsappController == null ? null : _openWhatsApp(),
        ChatTargetKind.task => _select(WorkspaceDestination.tasks),
        ChatTargetKind.approvals => _select(WorkspaceDestination.approvals),
        ChatTargetKind.file ||
        ChatTargetKind.files ||
        ChatTargetKind.memory =>
          _select(WorkspaceDestination.resources),
      };

  Widget _buildShell(BuildContext context) => ListenableBuilder(
        listenable:
            Listenable.merge([widget.controller, widget.developerController]),
        builder: (context, _) {
          final developer = widget.developerController;
          // Chat and voice offer "Open Developer" only where it is listed.
          final openDeveloper = _showDeveloper
              ? () => _select(WorkspaceDestination.developer)
              : null;
          return LayoutBuilder(builder: (context, constraints) {
            final expanded = constraints.maxWidth >= 840;
            final pages = [
              AssistantScreen(
                controller: widget.chatController,
                active: index == WorkspaceDestination.assistant,
                onOpenSettings: _openSettings,
                developer: widget.chatDeveloper,
                onOpenDeveloper: openDeveloper,
                onOpenReport: widget.library == null ? null : _openReport,
                onOpenTarget: _openChatTarget,
                dictation: widget.voiceController.speech,
              ),
              HomeScreen(
                controller: _home,
                sections: [
                  // Favorites and recent reports (docs/library.md).
                  if (widget.library case final repository?)
                    LibrarySection(
                        repository: repository, onOpenOther: _openFavorite),
                  // Tyre planning (docs/tyre-planning.md): its own screen.
                  AnumSurface(
                    onTap: () => Navigator.of(context).pushNamed('/tyres'),
                    semanticLabel: context.anum.t('tyreOpen'),
                    child: AnumRow(
                      icon: Icons.tire_repair_outlined,
                      title: context.anum.t('tyreOpen'),
                      subtitle: context.anum.t('tyreOpenHint'),
                      trailing: const Icon(Icons.chevron_right),
                    ),
                  ),
                ],
                // On wide screens the header sits above every page instead.
                header: expanded ? const SizedBox.shrink() : _header(),
                onCommand: _commandRequest,
                onVoice: _openCommand,
                onOpen: _openTarget,
                onSwitchWorkspace: _switchWorkspace,
                availableFeatures: _homeFeatures,
              ),
              TasksScreen(controller: widget.controller),
              VoiceScreen(
                  controller: widget.voiceController,
                  onOpenApprovals: () =>
                      _select(WorkspaceDestination.approvals),
                  onOpenDeveloper: openDeveloper),
              ApprovalsScreen(controller: widget.controller),
              AutomationsScreen(automations: _automations),
              ResourcesScreen(controller: widget.controller),
              if (developer != null)
                DeveloperScreen(
                  controller: developer,
                  active: index == WorkspaceDestination.developer,
                  onOpenSettings: _openSettings,
                )
              else
                const SizedBox.shrink(),
            ];
            final content = _content(context, pages);
            return Scaffold(
              key: _scaffold,
              drawer: _menu(context),
              body: expanded ? _expanded(context, content) : content,
              bottomNavigationBar: expanded ? null : _bottomBar(context),
            );
          });
        },
      );

  Widget _content(BuildContext context, List<Widget> pages) {
    final l = context.anum;
    return Column(children: [
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
  }

  /// Rail destinations, in [WorkspaceDestination] order.
  List<NavigationDestination> _railDestinations(AnumLocalizations l) => [
        NavigationDestination(
            icon: const Icon(Icons.chat_bubble_outline),
            selectedIcon: const Icon(Icons.chat_bubble),
            label: l.t('assistant')),
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
                isLabelVisible: widget.controller.pendingApprovals.isNotEmpty,
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

  Widget _expanded(BuildContext context, Widget content) {
    final l = context.anum;
    final p = context.palette;
    final destinations = _railDestinations(l);
    return AnumRail(
      rail: NavigationRail(
          // A deep link to Developer for a non-owner selects nothing: the
          // destination is not listed for them.
          selectedIndex: index < destinations.length ? index : null,
          onDestinationSelected: _select,
          labelType: NavigationRailLabelType.all,
          groupAlignment: -1,
          leading: Column(children: [
            const SizedBox(height: AnumSpacing.xs),
            const AnumLogoMark(size: 40, semanticLabel: 'ANUM 360'),
            const SizedBox(height: AnumSpacing.md),
            Semantics(
              button: true,
              selected: index == WorkspaceDestination.voice,
              label: l.t('shellCommand'),
              onTap: _openCommand,
              excludeSemantics: true,
              child: Tooltip(
                message: l.t('shellCommand'),
                excludeFromSemantics: true,
                child: InkWell(
                  key: const Key('rail-orb'),
                  customBorder: const CircleBorder(),
                  onTap: _openCommand,
                  child: const AnumOrb(size: 56),
                ),
              ),
            ),
            const SizedBox(height: AnumSpacing.xs),
          ]),
          trailing: Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.sm),
            child: IconButton(
                key: const Key('rail-menu'),
                tooltip: l.t('shellOpenMenu'),
                onPressed: () => _scaffold.currentState?.openDrawer(),
                icon: Icon(Icons.menu, color: p.muted)),
          ),
          destinations: destinations
              .map((item) => NavigationRailDestination(
                  icon: item.icon,
                  selectedIcon: item.selectedIcon,
                  label: Text(item.label)))
              .toList()),
      header: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsetsDirectional.fromSTEB(
              AnumSpacing.lg, AnumSpacing.sm, AnumSpacing.md, AnumSpacing.xs),
          child: _header(trailing: [
            IconButton(
                onPressed:
                    widget.controller.mutating ? null : widget.controller.load,
                tooltip: l.t('refresh'),
                icon: const Icon(Icons.refresh))
          ]),
        ),
      ),
      content: content,
    );
  }

  Widget _bottomBar(BuildContext context) {
    final l = context.anum;
    final selected = switch (index) {
      WorkspaceDestination.home => 0,
      WorkspaceDestination.assistant => 1,
      WorkspaceDestination.tasks => 2,
      WorkspaceDestination.voice => null,
      _ => 3,
    };
    return AnumBottomNav(
      selected: selected,
      orbSelected: index == WorkspaceDestination.voice,
      orbLabel: l.t('shellCommand'),
      onOrb: _openCommand,
      items: [
        AnumNavItem(
            key: const Key('nav-home'),
            icon: Icons.home_outlined,
            selectedIcon: Icons.home,
            label: l.t('home')),
        AnumNavItem(
            key: const Key('nav-chat'),
            icon: Icons.chat_bubble_outline,
            selectedIcon: Icons.chat_bubble,
            label: l.t('shellChat')),
        AnumNavItem(
            key: const Key('nav-tasks'),
            icon: Icons.task_alt_outlined,
            selectedIcon: Icons.task_alt,
            label: l.t('tasks')),
        AnumNavItem(
            key: const Key('nav-menu'),
            icon: Icons.menu,
            selectedIcon: Icons.menu_open,
            label: l.t('shellMenu'),
            badge: widget.controller.pendingApprovals.length),
      ],
      onSelected: (value) => switch (value) {
        0 => _select(WorkspaceDestination.home),
        1 => _select(WorkspaceDestination.assistant),
        2 => _select(WorkspaceDestination.tasks),
        _ => _scaffold.currentState?.openDrawer(),
      },
    );
  }

  void _openAgents() => _push(AgentsScreen(
      controller: widget.agentsController!,
      chatBuilder: widget.agentChat,
      callOpener: widget.agentCall));

  /// The Mobile Menu: the mockup's destinations, then everything else the
  /// app has, then the plan card.
  Widget _menu(BuildContext context) {
    final l = context.anum;
    void go(VoidCallback action) {
      _scaffold.currentState?.closeDrawer();
      action();
    }

    AnumMenuEntry destination(String key, IconData icon, int target,
            {int badge = 0}) =>
        AnumMenuEntry(
            key: Key('menu-$key'),
            icon: icon,
            label: l.t(key),
            selected: index == target,
            badge: badge,
            onTap: () => go(() => _select(target)));
    AnumMenuEntry page(String key, IconData icon, VoidCallback open) =>
        AnumMenuEntry(
            key: Key('menu-$key'),
            icon: icon,
            label: l.t(key),
            onTap: () => go(open));
    return AnumMenuDrawer(
      semanticLabel: l.t('shellMenuLabel'),
      sections: [
        AnumMenuSection(entries: [
          destination('home', Icons.home_outlined, WorkspaceDestination.home),
          // Agents (docs/agents.md); without the agents API, the installed skills.
          page(
              'shellAgents',
              Icons.smart_toy_outlined,
              widget.agentsController == null
                  ? () =>
                      _openResources(ResourcesTab.skills, l.t('shellAgents'))
                  : _openAgents),
          destination(
              'tasks', Icons.task_alt_outlined, WorkspaceDestination.tasks),
          if (widget.settingsController.team case final team?)
            AnumMenuEntry(
                key: const Key('menu-team'),
                icon: Icons.groups_outlined,
                label: TeamText.of(context).t('section'),
                onTap: () => go(() => _push(TeamHubScreen(repository: team)))),
          destination('shellAutomations', Icons.account_tree_outlined,
              WorkspaceDestination.automations),
          page('shellIntegrations', Icons.hub_outlined, _openIntegrations),
          if (widget.library != null) ...[
            page('shellKnowledge', Icons.menu_book_outlined, _openKnowledge),
            page('libFavorites', Icons.star_outline, _openFavorites),
            page('libReports', Icons.insert_chart_outlined, _openReports),
          ] else
            destination('shellKnowledge', Icons.menu_book_outlined,
                WorkspaceDestination.resources),
          destination(
              'shellCalls', Icons.call_outlined, WorkspaceDestination.voice),
          // Calendar & next actions (docs/calendar.md), a named route.
          page('calendar', Icons.calendar_month_outlined,
              () => Navigator.of(context).pushNamed('/calendar')),
          page('settings', Icons.settings_outlined, _openProfile),
        ]),
        AnumMenuSection(title: l.t('shellMore'), entries: [
          destination('approvals', Icons.approval_outlined,
              WorkspaceDestination.approvals,
              badge: widget.controller.pendingApprovals.length),
          destination('assistant', Icons.chat_bubble_outline,
              WorkspaceDestination.assistant),
          if (widget.emailController != null) ...[
            page('inbox', Icons.mail_outline, _openInbox),
            page('emailSummary', Icons.mark_email_unread_outlined,
                () => Navigator.of(context).pushNamed('/email/summary')),
            // The Email Center on the workspace's own mail server
            // (docs/email.md#email-center), a named route.
            page('mc.title', Icons.dns_outlined,
                () => Navigator.of(context).pushNamed('/mail')),
          ],
          if (widget.whatsappController != null)
            page('whatsapp', Icons.forum_outlined, _openWhatsApp),
          if (widget.marketingController != null)
            page('mk.title', Icons.campaign_outlined, _openMarketing),
          if (widget.financeController != null)
            page('financeMenu', Icons.account_balance_wallet_outlined,
                _openFinance),
          if (_showDeveloper)
            destination('developer', Icons.construction_outlined,
                WorkspaceDestination.developer),
          page('organization', Icons.admin_panel_settings_outlined,
              () => _openGovernance(GovernanceSection.policies)),
          page('shellMarketplace', Icons.storefront_outlined,
              () => _openGovernance(GovernanceSection.marketplace)),
          page('shellRouting', Icons.route,
              () => _openGovernance(GovernanceSection.routing)),
        ]),
      ],
      footer: AnumPlanCard(
        key: const Key('menu-plan'),
        title: l.t('planFree'),
        subtitle: widget.workspaceName ?? l.t('planFreeShort'),
        onTap: () => go(() =>
            _push(SubscriptionScreen(workspaceName: widget.workspaceName))),
      ),
    );
  }

  void _select(int value) {
    setState(() => _index.value = value);
    // After a workspace switch (Settings) the role is checked again, so
    // Developer appears or disappears with it.
    widget.developerController?.ensureOwner();
  }

  @override
  void dispose() {
    _index.dispose();
    if (_ownsAutomations) _automations.dispose();
    _integrations?.dispose();
    if (_ownsHome) _home.dispose();
    if (widget.themeController == null) _theme.dispose();
    super.dispose();
  }
}

/// The wide layout: the rail, a divider, and the header above the page.
class AnumRail extends StatelessWidget {
  const AnumRail(
      {required this.rail,
      required this.header,
      required this.content,
      super.key});
  final Widget rail, header, content;

  @override
  Widget build(BuildContext context) => Row(children: [
        SafeArea(right: false, child: rail),
        const VerticalDivider(width: 1),
        Expanded(child: Column(children: [header, Expanded(child: content)])),
      ]);
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
              padding: const EdgeInsets.symmetric(
                  horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
              child: Row(children: [
                Icon(icon, size: 18),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                    child: Text(text,
                        style: Theme.of(context).textTheme.bodySmall))
              ]))));
}
