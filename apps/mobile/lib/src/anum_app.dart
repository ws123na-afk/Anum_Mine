import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import '../data/api_client.dart';
import '../data/session_store.dart';
import '../features/autonomy/autonomy_repository.dart';
import '../features/meetings/meetings_repository.dart';
import '../features/auth/auth_controller.dart';
import '../features/automations/automation_repository.dart';
import '../features/automations/automations_controller.dart';
import '../features/auth/auth_repository.dart';
import '../features/auth/auth_screens.dart';
import '../features/auth/intro.dart';
import '../features/auth/oidc.dart';
import '../features/chat/chat_controller.dart';
import '../features/command/command.dart';
import '../features/chat/chat_repository.dart';
import '../features/agents/agent_chat.dart';
import '../features/agents/agents_controller.dart';
import '../features/agents/agents_repository.dart';
import '../features/agents/agents_screen.dart';
import '../features/developer/developer_assist.dart';
import '../features/developer/developer_controller.dart';
import '../features/developer/developer_repository.dart';
import '../features/tyre/tyre_controller.dart';
import '../features/tyre/tyre_repository.dart';
import '../features/tyre/tyre_screen.dart';
import '../features/whatsapp/whatsapp_controller.dart';
import '../features/whatsapp/whatsapp_repository.dart';
import '../features/email/email_controller.dart';
import '../features/email/email_repository.dart';
import '../features/email/inbox_screen.dart';
import '../features/commerce/commerce_controller.dart';
import '../features/commerce/commerce_repository.dart';
import '../features/finance/finance_controller.dart';
import '../features/finance/finance_intel_models.dart';
import '../features/finance/finance_repository.dart';
import '../features/finance/finance_screen.dart';
import '../features/team/team_screens.dart' show NotificationsScreen;
import '../features/reports/workspace_tool_routes.dart';
import '../features/business/business_controller.dart';
import '../features/business/business_repository.dart';
import '../features/business/support_controller.dart';
import '../features/business/support_repository.dart';
import '../features/brevo/brevo_data.dart';
import '../features/brevo/brevo_screen.dart';
import '../features/marketing/marketing_controller.dart';
import '../features/marketing/marketing_repository.dart';
import '../features/workspace/api_workspace_repository.dart';
import '../features/team/team_repository.dart';
import '../features/workspace/destinations.dart';
import '../features/workspace/files_repository.dart';
import '../features/workspace/workspace_controller.dart';
import '../features/workspace/workspace_home.dart';
import '../features/workspace/tasks_screen.dart';
import '../features/voice/call_controller.dart';
import '../features/voice/call_screen.dart';
import '../features/voice/speech_service.dart';
import '../features/voice/voice_controller.dart';
import '../features/voice/voice_repository.dart';
import '../features/settings/profile_screen.dart';
import '../features/settings/settings_controller.dart';
import '../features/admin/admin_repository.dart';
import '../features/admin/org_admin_repository.dart';
import '../features/learning/learning_repository.dart';
import '../features/privacy/privacy_repository.dart';
import '../features/library/library_repository.dart';
import '../features/push/firebase_push_messaging.dart';
import '../features/push/push_controller.dart';
import '../features/push/push_models.dart';
import '../features/team_calls/call_controller.dart' as calls_feature;
import '../features/team_calls/call_media.dart';
import '../features/team_calls/call_repository.dart';
import '../features/team_calls/call_screens.dart';
import '../features/push/push_repository.dart';
import '../features/governance/api_governance_repository.dart';
import '../features/governance/governance_controller.dart';
import 'app_environment.dart';
import 'infrastructure/appauth_authenticator.dart';
import 'infrastructure/http_event_stream.dart';
import 'infrastructure/mobile_adapters.dart';
import 'theme/anum_theme.dart';
import 'theme/theme_controller.dart';
import 'localization/anum_localizations.dart';
import '../features/agent_controls/agent_controls_data.dart';

abstract final class AnumRoutes {
  static const splash = '/splash';
  static const onboarding = '/onboarding';
  static const signIn = '/sign-in';
  static const createAccount = '/create-account';
  static const workspaceSetup = '/workspace-setup';
  static const modelSetup = '/model-setup';
  static const assistant = '/assistant';
  static const home = '/home';
  static const tasks = '/tasks';
  static const taskDetail = '/tasks/:id';
  static const approvals = '/approvals';
  static const automations = '/automations';
  static const files = '/files';
  static const voice = '/voice';
  static const developer = '/developer';
  static const inbox = '/inbox';
  static const profile = '/profile';

  /// Tyre planning (docs/tyre-planning.md); `/tyres/alerts` opens its alerts.
  static const tyres = '/tyres';
  static const tyreAlerts = '/tyres/alerts';

  /// Finance, Trading intelligence (docs/finance-trading-intel.md#daily-report):
  /// where the daily trading report push opens.
  static const financeIntel = '/finance/intel';
  static const command = '/command';

  /// In-app calls with teammates (docs/in-app-calls.md); call pushes open it.
  static const teamCalls = '/team-calls';
  static const quickCommands = '/quick-commands';
}

/// API origin a release build uses when `ANUM_API_URL` is not defined: the
/// owner's production API (docs/mobile.md, "Release builds"). A
/// `--dart-define=ANUM_API_URL=...` (CI: the `ANUM_PRODUCTION_API_URL`
/// repository variable) overrides it.
const productionApiUrl = 'https://api.anum360os.com/';

/// Debug and profile builds without `ANUM_API_URL`: the API on the
/// development machine, as the Android emulator sees it.
const developmentApiUrl = 'http://10.0.2.2:8000/';

/// The API origin for a defined `ANUM_API_URL` (empty when not defined),
/// always ending in one `/` so relative API paths resolve under it.
String resolveApiUrl(String definedUrl, {required bool release}) {
  final url = definedUrl.trim();
  if (url.isEmpty) return release ? productionApiUrl : developmentApiUrl;
  return '${url.replaceFirst(RegExp(r'/+$'), '')}/';
}

const _developmentHosts = {'localhost', '127.0.0.1', '10.0.2.2', '0.0.0.0'};

/// Why this build's configuration cannot run, or null when it can. Release
/// builds need an HTTPS API and an HTTPS Keycloak issuer on real hosts, so a
/// release build can never land on the local development sign-in.
String? releaseConfigError({
  required String apiUrl,
  required OidcConfig? oidc,
  required bool release,
}) {
  if (!release) return null;
  final apiError = releaseApiUrlError(apiUrl, release: true);
  if (apiError != null) return apiError;
  if (_developmentHosts.contains(Uri.parse(apiUrl).host)) {
    return 'This build is misconfigured: ANUM_API_URL points at a development '
        'machine ("$apiUrl"). Rebuild with the production API address.';
  }
  if (oidc == null) {
    return 'This build is not configured for sign-in: ANUM_OIDC_ISSUER is '
        'missing. Rebuild with --dart-define=ANUM_OIDC_ISSUER=https://...';
  }
  final issuer = Uri.tryParse(oidc.issuer);
  if (issuer == null ||
      issuer.scheme != 'https' ||
      issuer.host.isEmpty ||
      _developmentHosts.contains(issuer.host)) {
    return 'This build is misconfigured: ANUM_OIDC_ISSUER must be the '
        'production https:// Keycloak issuer (got "${oidc.issuer}").';
  }
  return null;
}

/// Why [apiUrl] cannot be used by a release build, or null when it can.
/// Release builds talk to the API over HTTPS only.
String? releaseApiUrlError(String apiUrl, {required bool release}) {
  if (!release) return null;
  final uri = Uri.tryParse(apiUrl);
  if (uri != null && uri.scheme == 'https' && uri.host.isNotEmpty) return null;
  return 'This build is misconfigured: ANUM_API_URL must be an https:// '
      'address in release builds (got "$apiUrl"). Rebuild with '
      '--dart-define=ANUM_API_URL=https://...';
}

class AnumApp extends StatefulWidget {
  const AnumApp({super.key, this.environment = const AnumEnvironment()});

  /// What the app talks to; the default is the configured API. Journey tests
  /// pass a fake HTTP client here (docs/mobile-journeys.md).
  final AnumEnvironment environment;

  @override
  State<AnumApp> createState() => _AnumAppState();
}

class _AnumAppState extends State<AnumApp> {
  late final AuthController auth;
  late final WorkspaceController workspace;
  late final AutomationsController automations;
  late final ChatController chat;
  late final VoiceController voice;
  late final SettingsController settings;
  late final GovernanceController governance;
  late final LocaleController locale;
  late final ThemeController theme;
  late final IntroController intro;
  late final DeveloperController developer;
  late final MarketingController marketing;
  late final BusinessController business;
  late final DeveloperAssist chatDeveloper;
  late final DeveloperAssist voiceDeveloper;
  late final LibraryRepository library;
  late final PushController push;
  late final CommerceController commerce;
  late final FinanceController finance;
  late final TyreController tyre;
  late final AgentsController agents;
  late final AgentChatBuilder agentChat;
  late final AgentCallOpener agentCall;
  late final calls_feature.AppCallsController appCalls;

  /// For push deep links and foreground notices, which arrive outside any
  /// widget's context.
  final _navigator = GlobalKey<NavigatorState>();
  final _messenger = GlobalKey<ScaffoldMessengerState>();
  late final WhatsAppController whatsapp;
  late final EmailController email;
  late final CommandController command;
  late final WorkspaceToolRoutes tools;

  /// Set when the build's configuration is unsafe; the app then shows only
  /// this message and creates no clients.
  String? _configError;

  @override
  void initState() {
    super.initState();
    final env = widget.environment;
    final configuredUrl = resolveApiUrl(
      env.apiUrl ?? const String.fromEnvironment('ANUM_API_URL'),
      release: kReleaseMode,
    );
    // One client per call site, as before; a test environment hands out its
    // fake. Null keeps each adapter's own default.
    final newClient = env.httpClient;
    // Debug and profile builds without --dart-define=ANUM_OIDC_ISSUER keep
    // the local development sign-in; release builds always use Keycloak
    // (the production issuer unless the define names another).
    final oidcConfig = OidcConfig.fromEnvironment();
    _configError = releaseConfigError(
      apiUrl: configuredUrl,
      oidc: oidcConfig,
      release: kReleaseMode,
    );
    if (_configError != null) return;
    final oidc =
        oidcConfig == null ? null : AppAuthOidcAuthenticator(oidcConfig);
    final secureSessions = SecureSessionStore();
    final SessionStore sessions = oidc == null
        ? secureSessions
        : OidcSessionStore(inner: secureSessions, authenticator: oidc);
    final api = AnumApiClient(
      baseUri: Uri.parse(configuredUrl),
      transport: HttpApiTransport(client: newClient?.call()),
      sessions: sessions,
    );
    // Phone push (docs/notifications.md): off unless this build carries the
    // owner's Firebase files and the server has ANUM_FCM_SERVICE_ACCOUNT_JSON.
    push = PushController(
      repository: PushRepository(api),
      messaging: env.pushMessaging ?? FirebasePushMessaging(),
      store: SharedPreferencesPushLocalStore(),
    )
      ..onOpen = _openPush
      ..onNotice = _showPushNotice;
    final authRepository = AuthRepository(
      api: api,
      sessions: sessions,
      oidc: oidc,
      workspacePreferences: SecureWorkspacePreferenceStore(),
      sessionListener: push,
    );
    auth = AuthController(authRepository);
    automations = AutomationsController(ApiAutomationTriggerRepository(api));
    final fileTransfer = HttpWorkspaceFileTransfer(
      baseUri: Uri.parse(configuredUrl),
      sessions: sessions,
      client: newClient?.call(),
      uploadClient: newClient,
    );
    workspace = WorkspaceController(
      ApiWorkspaceRepository(api, fileTransfer: fileTransfer),
      files: ApiFilesRepository(api, transfer: fileTransfer),
      meetings: ApiMeetingsRepository(api, transfer: fileTransfer),
    );
    // Streamed replies for the Assistant and voice (docs/conversations.md).
    final events = HttpEventStreamClient(
      baseUri: Uri.parse(configuredUrl),
      sessions: sessions,
      clientFactory: newClient,
    );
    chat = ChatController(ChatRepository(api, events));
    // User-built agents (docs/agents.md) and the chat on each agent's threads.
    agents = AgentsController(ApiAgentsRepository(api));
    agentChat = (context, agent) => AgentChat(
          agent: agent,
          repository: (agentId) =>
              ChatRepository(api, events, agentId: agentId),
        );
    // Call on an agent opens the voice call screen (docs/voice.md#call-mode).
    agentCall = (context, agent) => openCall(
          context,
          create: () => CallController(
              repository: voice.repository, speech: voice.speech),
          locale: voice.locale,
          name: agent.name,
          openSettings: voice.openSettings,
        );
    voice = VoiceController(
      repository: VoiceRepository(api, events: events),
      speech: DeviceSpeechService(),
    );
    settings = SettingsController(
      authRepository,
      admin: ApiAdminRepository(api),
      orgAdmin: ApiOrgAdminRepository(api),
      autonomy: ApiAutonomyRepository(api),
      privacy: ApiPrivacyRepository(api, saver: const FilePickerExportSaver()),
      push: push,
      learning: ApiLearningRepository(api),
      team: ApiTeamRepository(api),
      agentControls: ApiAgentControlsRepository(api),
    );
    workspace.onApprovalDecided = _offerPushAfterApproval;
    // In-app calls between teammates (docs/in-app-calls.md#flutter).
    appCalls = calls_feature.AppCallsController(
        repository: ApiCallRepository(api,
            sessions: sessions, clientFactory: newClient),
        mediaFactory: WebRtcCallMedia.new);
    governance = GovernanceController(
      ApiGovernanceRepository(
        api,
        auditExporter: HttpAuditExporter(
          baseUri: Uri.parse(configuredUrl),
          sessions: sessions,
          client: newClient?.call(),
        ),
      ),
    );
    // Developer mode (docs/developer-mode.md): the screen, plus "build ..." in
    // the Assistant and "developer, ..." by voice. The role is checked once.
    // Commerce (docs/commerce.md): sales summary and orders, read-only here.
    commerce = CommerceController(ApiCommerceRepository(api));
    // Finance (docs/finance.md): the person's own ledger, read only.
    finance = FinanceController(ApiFinanceRepository(api),
        intelRepository: ApiFinanceIntelRepository(api));
    // The post-close trading report notice opens Finance, Trading
    // intelligence, where the daily reports are listed.
    NotificationsScreen.openers['finance_daily_report'] = (context) async {
      final loading = finance.select(FinanceSection.intel);
      await Navigator.push(
          context,
          MaterialPageRoute<void>(
              builder: (_) => FinanceScreen(controller: finance)));
      await loading;
    };
    tyre = TyreController(ApiTyreRepository(api));
    final developerRepository = ApiDeveloperRepository(api);
    developer = DeveloperController(developerRepository);
    // Social media marketing (docs/marketing.md).
    marketing = MarketingController(ApiMarketingRepository(api),
        brevo: BrevoController(ApiBrevoRepository(api)));
    // Business, the internal sales desk (docs/business.md).
    business = BusinessController(ApiBusinessRepository(api),
        support: SupportController(ApiSupportRepository(api)));
    chatDeveloper = DeveloperAssist(
      repository: developerRepository,
      source: 'text',
      isOwner: developer.ensureOwner,
    );
    voiceDeveloper = DeveloperAssist(
      repository: developerRepository,
      source: 'voice',
      isOwner: developer.ensureOwner,
    );
    voiceDeveloper.announce = (text) => voice.sayLocal(text);
    voice.developer = voiceDeveloper;
    // WhatsApp inbox (docs/whatsapp.md): owners and members; owners approve.
    whatsapp = WhatsAppController(ApiWhatsAppRepository(api));
    email = EmailController(ApiEmailRepository(api));
    // ANUM Command (docs/command.md): app-wide, so a command keeps being
    // followed after "Keep working in background".
    command = CommandController(
      repository: ApiCommandRepository(api),
      speech: DeviceSpeechService(),
      transfer: HttpCommandTransfer(
        baseUri: Uri.parse(configuredUrl),
        sessions: sessions,
        upload: fileTransfer.upload,
        clientFactory: newClient,
      ),
    )..onSettled = _commandSettled;
    library = ApiLibraryRepository(api, saver: const FilePickerExportSaver());
    tools = WorkspaceToolRoutes(
        api: api,
        library: library,
        email: email,
        mailTransfer: HttpMailFileTransfer(
            baseUri: Uri.parse(configuredUrl),
            sessions: sessions,
            files: fileTransfer,
            client: newClient?.call()));
    locale = LocaleController()..restore();
    // Pushes use the app's language (docs/notifications.md#phone-push).
    push.languageOf = () => (locale.locale?.languageCode ??
                PlatformDispatcher.instance.locale.languageCode) ==
            'ar'
        ? 'ar'
        : 'en';
    locale.addListener(push.languageChanged);
    theme = ThemeController()..restore();
    intro = IntroController()..restore();
    auth.addListener(_onAuthChanged);
    auth.restore();
    push.start();
  }

  void _onAuthChanged() {
    if (auth.phase == AuthPhase.ready && workspace.phase.name == 'initial') {
      workspace.load();
    }
    if (auth.phase == AuthPhase.ready) unawaited(appCalls.start());
    if (auth.phase == AuthPhase.ready && !_pushReady) {
      _pushReady = true;
      // Registers when this phone already allows notifications; never asks.
      push.refresh().then((_) {
        final pending = push.takePendingDestination();
        if (pending != null) _openPush(pending);
      });
    }
    if (auth.phase == AuthPhase.signedOut) {
      if (_pushReady) push.signedOut();
      _pushReady = false;
      workspace.currentUserId = null;
      appCalls.stop();
      // The next person to sign in never sees these conversations.
      chat.reset();
      automations.reset();
      developer.reset();
      commerce.reset();
      finance.reset();
      tyre.reset();
      marketing.reset();
      business.reset();
      chatDeveloper.reset();
      voiceDeveloper.reset();
      whatsapp.reset();
      command.reset();
      tools.reset();
    }
    if (auth.phase == AuthPhase.ready && workspace.currentUserId == null) {
      _rememberUser();
    }
  }

  bool _pushReady = false;

  /// The home orb and ANUM Command tile open the Command flow.
  void _openCommand() => _navigator.currentState?.pushNamed(AnumRoutes.command);

  /// A followed command finished or needs approval while its screen is closed.
  void _commandSettled(CommandView view) {
    if (command.onScreen) return;
    _messenger.currentState?.showSnackBar(
      SnackBar(
        content: Text(commandSettledNotice(view, locale.locale)),
        action: SnackBarAction(
          label: CommandStrings(locale.locale?.languageCode == 'ar')
              .t('openRunning'),
          onPressed: () =>
              _navigator.currentState?.pushNamed(AnumRoutes.command),
        ),
      ),
    );
  }

  /// A tapped push: open the approval queue, the task or the developer
  /// request it names, in the workspace it belongs to.
  Future<void> _openPush(PushDestination destination) async {
    if (auth.phase != AuthPhase.ready) {
      push.pendingDestination = destination;
      return;
    }
    final session = await auth.repository.restoreSession();
    final current = session?.context.workspaceId;
    if (current != null && current != destination.workspaceId) {
      _messenger.currentState?.showSnackBar(
        SnackBar(
          content: Text(
            'That notification is for workspace '
            '"${destination.workspaceId}". Switch to it in Settings to open it.',
          ),
        ),
      );
      return;
    }
    _navigator.currentState?.pushNamed(destination.route);
  }

  void _showPushNotice(PushNotice notice) {
    final destination = notice.destination;
    _messenger.currentState?.showSnackBar(
      SnackBar(
        content: Text(
          notice.body.isEmpty
              ? notice.title
              : '${notice.title}: ${notice.body}',
        ),
        action: destination == null
            ? null
            : SnackBarAction(
                label: 'Open',
                onPressed: () => _openPush(destination),
              ),
      ),
    );
  }

  /// After the person's first approval decision, offer phone push once.
  Future<void> _offerPushAfterApproval() async {
    if (!await push.shouldOfferAfterApproval()) return;
    _messenger.currentState?.showSnackBar(
      SnackBar(
        duration: const Duration(seconds: 10),
        content: const Text(
          'Get a notification on this phone when an approval is waiting?',
        ),
        action: SnackBarAction(label: 'Turn on', onPressed: push.enable),
      ),
    );
  }

  /// Who is signed in, for the approval cards (one approval per person).
  Future<void> _rememberUser() async {
    try {
      final session = await auth.repository.restoreSession();
      workspace.currentUserId = session?.context.userId;
      appCalls.selfUserId = session?.context.userId;
    } on Object {
      // Unknown user: the API still refuses a second approval (409).
    }
  }

  @override
  void dispose() {
    if (_configError != null) {
      super.dispose();
      return;
    }
    auth.removeListener(_onAuthChanged);
    auth.dispose();
    workspace.dispose();
    automations.dispose();
    chat.dispose();
    voice.dispose();
    settings.dispose();
    governance.dispose();
    developer.dispose();
    commerce.dispose();
    finance.dispose();
    tyre.dispose();
    marketing.dispose();
    business.dispose();
    push.dispose();
    chatDeveloper.dispose();
    voiceDeveloper.dispose();
    whatsapp.dispose();
    email.dispose();
    command.dispose();
    tools.dispose();
    appCalls.dispose();
    locale.dispose();
    theme.dispose();
    intro.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final configError = _configError;
    if (configError != null) {
      return MaterialApp(
        title: 'ANUM',
        debugShowCheckedModeBanner: false,
        theme: AnumTheme.dark(),
        home: Scaffold(
          body: SafeArea(
            child: Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(configError, textAlign: TextAlign.center),
              ),
            ),
          ),
        ),
      );
    }
    return _buildApp(context);
  }

  Widget _buildApp(BuildContext context) => ListenableBuilder(
        listenable: Listenable.merge([locale, theme]),
        builder: (context, _) => MaterialApp(
          navigatorKey: _navigator,
          scaffoldMessengerKey: _messenger,
          title: 'ANUM',
          debugShowCheckedModeBanner: false,
          theme: AnumTheme.light(),
          darkTheme: AnumTheme.dark(),
          themeMode: theme.mode,
          locale: locale.locale,
          restorationScopeId: 'anum_mobile',
          supportedLocales: const [Locale('en'), Locale('ar')],
          localizationsDelegates: const [
            AnumLocalizations.delegate,
            ...GlobalMaterialLocalizations.delegates,
          ],
          onGenerateRoute: _onGenerateRoute,
          // The ring and the call in progress stay above every screen.
          builder: (context, child) => AppCallOverlay(
              controller: appCalls, child: child ?? const SizedBox.shrink()),
          home: ListenableBuilder(
            listenable: auth,
            builder: (context, _) => auth.phase == AuthPhase.ready
                ? WorkspaceHome(
                    controller: workspace,
                    chatController: chat,
                    voiceController: voice,
                    settingsController: settings,
                    governanceController: governance,
                    localeController: locale,
                    themeController: theme,
                    developerController: developer,
                    automationsController: automations,
                    marketingController: marketing,
                    businessController: business,
                    chatDeveloper: chatDeveloper,
                    whatsappController: whatsapp,
                    emailController: email,
                    commerceController: commerce,
                    financeController: finance,
                    agentsController: agents,
                    agentChat: agentChat,
                    agentCall: agentCall,
                    workspaceName: auth.workspaceName,
                    onOpenCommand: _openCommand,
                    library: library,
                  )
                : AuthFlow(controller: auth, intro: intro),
          ),
        ),
      );

  Route<void>? _onGenerateRoute(RouteSettings settings) {
    final name = settings.name;
    if ({
      AnumRoutes.splash,
      AnumRoutes.onboarding,
      AnumRoutes.signIn,
      AnumRoutes.workspaceSetup,
      AnumRoutes.modelSetup,
    }.contains(name)) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => AuthFlow(controller: auth, intro: intro),
      );
    }
    if (name == AnumRoutes.createAccount) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => ListenableBuilder(
          listenable: auth,
          builder: (_, __) => auth.phase == AuthPhase.signedOut
              ? SignInScreen(controller: auth, startWithRegistration: true)
              : AuthFlow(controller: auth, intro: intro),
        ),
      );
    }
    if (name == AnumRoutes.profile) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
          auth: auth,
          child: ProfileScreen(
            settings: this.settings,
            locale: locale,
            theme: theme,
            workspaceName: auth.workspaceName,
          ),
        ),
      );
    }
    final index = switch (name) {
      AnumRoutes.assistant => WorkspaceDestination.assistant,
      AnumRoutes.home => WorkspaceDestination.home,
      AnumRoutes.tasks => WorkspaceDestination.tasks,
      AnumRoutes.voice => WorkspaceDestination.voice,
      AnumRoutes.approvals => WorkspaceDestination.approvals,
      AnumRoutes.automations => WorkspaceDestination.automations,
      AnumRoutes.files => WorkspaceDestination.resources,
      AnumRoutes.developer => WorkspaceDestination.developer,
      _ => null,
    };
    if (name == AnumRoutes.financeIntel) {
      unawaited(finance.select(FinanceSection.intel));
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
            auth: auth, child: FinanceScreen(controller: finance)),
      );
    }
    if (name == AnumRoutes.tyres || name == AnumRoutes.tyreAlerts) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
          auth: auth,
          child: TyreScreen(
            controller: tyre,
            initialTab: name == AnumRoutes.tyreAlerts ? 2 : 0,
          ),
        ),
      );
    }
    if (index != null) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) =>
            _AuthenticatedDestination(auth: auth, child: _workspace(index)),
      );
    }
    if (name == AnumRoutes.command || name == AnumRoutes.quickCommands) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
          auth: auth,
          child: name == AnumRoutes.command
              ? CommandScreen(controller: command)
              : QuickCommandsScreen(controller: command),
        ),
      );
    }
    final tool = tools.route(
      settings,
      (child) => _AuthenticatedDestination(auth: auth, child: child),
    );
    if (tool != null) return tool;
    if (name == AnumRoutes.teamCalls) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
            auth: auth, child: TeamCallsScreen(controller: appCalls)),
      );
    }
    if (name == AnumRoutes.inbox) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
          auth: auth,
          child: InboxScreen(controller: email),
        ),
      );
    }
    if (name != null &&
        name.startsWith('/tasks/') &&
        name.length > '/tasks/'.length) {
      final taskId = Uri.decodeComponent(name.substring('/tasks/'.length));
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
          auth: auth,
          child: _TaskDeepLink(controller: workspace, taskId: taskId),
        ),
      );
    }
    return null;
  }

  Widget _workspace(int index) => WorkspaceHome(
        controller: workspace,
        chatController: chat,
        voiceController: voice,
        settingsController: settings,
        governanceController: governance,
        localeController: locale,
        themeController: theme,
        developerController: developer,
        automationsController: automations,
        marketingController: marketing,
        businessController: business,
        chatDeveloper: chatDeveloper,
        whatsappController: whatsapp,
        emailController: email,
        commerceController: commerce,
        financeController: finance,
        agentsController: agents,
        agentChat: agentChat,
        agentCall: agentCall,
        workspaceName: auth.workspaceName,
        onOpenCommand: _openCommand,
        library: library,
        initialIndex: index,
      );
}

class _AuthenticatedDestination extends StatelessWidget {
  const _AuthenticatedDestination({required this.auth, required this.child});
  final AuthController auth;
  final Widget child;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: auth,
        builder: (_, __) =>
            auth.phase == AuthPhase.ready ? child : AuthFlow(controller: auth),
      );
}

class _TaskDeepLink extends StatefulWidget {
  const _TaskDeepLink({required this.controller, required this.taskId});
  final WorkspaceController controller;
  final String taskId;
  @override
  State<_TaskDeepLink> createState() => _TaskDeepLinkState();
}

class _TaskDeepLinkState extends State<_TaskDeepLink> {
  late final future = widget.controller.loadTask(widget.taskId);
  @override
  Widget build(BuildContext context) => FutureBuilder(
        future: future,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Scaffold(
              appBar: AppBar(title: const Text('Task detail')),
              body:
                  Center(child: Text('Unable to load task: ${snapshot.error}')),
            );
          }
          final task = snapshot.data;
          return task == null
              ? const Scaffold(body: Center(child: CircularProgressIndicator()))
              : TaskDetailScreen(controller: widget.controller, task: task);
        },
      );
}
