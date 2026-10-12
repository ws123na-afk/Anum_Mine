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
import '../features/workspace/api_workspace_repository.dart';
import '../features/team/team_repository.dart';
import '../features/workspace/destinations.dart';
import '../features/workspace/files_repository.dart';
import '../features/workspace/workspace_controller.dart';
import '../features/workspace/workspace_home.dart';
import '../features/workspace/tasks_screen.dart';
import '../features/voice/speech_service.dart';
import '../features/voice/voice_controller.dart';
import '../features/voice/voice_repository.dart';
import '../features/settings/profile_screen.dart';
import '../features/settings/settings_controller.dart';
import '../features/admin/admin_repository.dart';
import '../features/learning/learning_repository.dart';
import '../features/privacy/privacy_repository.dart';
import '../features/library/library_repository.dart';
import '../features/push/firebase_push_messaging.dart';
import '../features/push/push_controller.dart';
import '../features/push/push_models.dart';
import '../features/push/push_repository.dart';
import '../features/governance/api_governance_repository.dart';
import '../features/governance/governance_controller.dart';
import 'infrastructure/appauth_authenticator.dart';
import 'infrastructure/http_event_stream.dart';
import 'infrastructure/mobile_adapters.dart';
import 'theme/anum_theme.dart';
import 'theme/theme_controller.dart';
import 'localization/anum_localizations.dart';

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
  static const command = '/command';
  static const quickCommands = '/quick-commands';
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
  const AnumApp({super.key});

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
  late final DeveloperAssist chatDeveloper;
  late final DeveloperAssist voiceDeveloper;
  late final LibraryRepository library;
  late final PushController push;
  late final CommerceController commerce;
  late final TyreController tyre;

  /// For push deep links and foreground notices, which arrive outside any
  /// widget's context.
  final _navigator = GlobalKey<NavigatorState>();
  final _messenger = GlobalKey<ScaffoldMessengerState>();
  late final WhatsAppController whatsapp;
  late final EmailController email;
  late final CommandController command;

  /// Set when the build's configuration is unsafe; the app then shows only
  /// this message and creates no clients.
  String? _configError;

  @override
  void initState() {
    super.initState();
    const configuredUrl = String.fromEnvironment(
      'ANUM_API_URL',
      defaultValue: 'http://10.0.2.2:8000/',
    );
    _configError = releaseApiUrlError(configuredUrl, release: kReleaseMode);
    if (_configError != null) return;
    // With --dart-define=ANUM_OIDC_ISSUER the app signs in through Keycloak;
    // without it, the local development sign-in stays in place.
    final oidcConfig = OidcConfig.fromEnvironment();
    final oidc =
        oidcConfig == null ? null : AppAuthOidcAuthenticator(oidcConfig);
    final secureSessions = SecureSessionStore();
    final SessionStore sessions = oidc == null
        ? secureSessions
        : OidcSessionStore(inner: secureSessions, authenticator: oidc);
    final api = AnumApiClient(
      baseUri: Uri.parse(configuredUrl),
      transport: HttpApiTransport(),
      sessions: sessions,
    );
    // Phone push (docs/notifications.md): off unless this build carries the
    // owner's Firebase files and the server has ANUM_FCM_SERVICE_ACCOUNT_JSON.
    push = PushController(
        repository: PushRepository(api),
        messaging: FirebasePushMessaging(),
        store: SharedPreferencesPushLocalStore())
      ..onOpen = _openPush
      ..onNotice = _showPushNotice;
    final authRepository = AuthRepository(
        api: api,
        sessions: sessions,
        oidc: oidc,
        workspacePreferences: SecureWorkspacePreferenceStore(),
        sessionListener: push);
    auth = AuthController(authRepository);
    automations = AutomationsController(ApiAutomationRepository(api));
    final fileTransfer = HttpWorkspaceFileTransfer(
      baseUri: Uri.parse(configuredUrl),
      sessions: sessions,
    );
    workspace = WorkspaceController(
      ApiWorkspaceRepository(api, fileTransfer: fileTransfer),
      files: ApiFilesRepository(api, transfer: fileTransfer),
      meetings: ApiMeetingsRepository(api, transfer: fileTransfer),
    );
    // Streamed replies for the Assistant and voice (docs/conversations.md).
    final events = HttpEventStreamClient(
        baseUri: Uri.parse(configuredUrl), sessions: sessions);
    chat = ChatController(ChatRepository(api, events));
    voice = VoiceController(
        repository: VoiceRepository(api, events: events),
        speech: DeviceSpeechService());
    settings = SettingsController(authRepository,
        admin: ApiAdminRepository(api),
        autonomy: ApiAutonomyRepository(api),
        privacy:
            ApiPrivacyRepository(api, saver: const FilePickerExportSaver()),
        push: push,
        learning: ApiLearningRepository(api),
        team: ApiTeamRepository(api));
    workspace.onApprovalDecided = _offerPushAfterApproval;
    governance = GovernanceController(ApiGovernanceRepository(
      api,
      auditExporter: HttpAuditExporter(
          baseUri: Uri.parse(configuredUrl), sessions: sessions),
    ));
    // Developer mode (docs/developer-mode.md): the screen, plus "build ..." in
    // the Assistant and "developer, ..." by voice. The role is checked once.
    // Commerce (docs/commerce.md): sales summary and orders, read-only here.
    commerce = CommerceController(ApiCommerceRepository(api));
    tyre = TyreController(ApiTyreRepository(api));
    final developerRepository = ApiDeveloperRepository(api);
    developer = DeveloperController(developerRepository);
    chatDeveloper = DeveloperAssist(
        repository: developerRepository,
        source: 'text',
        isOwner: developer.ensureOwner);
    voiceDeveloper = DeveloperAssist(
        repository: developerRepository,
        source: 'voice',
        isOwner: developer.ensureOwner);
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
            upload: fileTransfer.upload))
      ..onSettled = _commandSettled;
    library = ApiLibraryRepository(api, saver: const FilePickerExportSaver());
    locale = LocaleController()..restore();
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
      // The next person to sign in never sees these conversations.
      chat.reset();
      automations.reset();
      developer.reset();
      commerce.reset();
      tyre.reset();
      chatDeveloper.reset();
      voiceDeveloper.reset();
      whatsapp.reset();
      command.reset();
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
    _messenger.currentState?.showSnackBar(SnackBar(
        content: Text(commandSettledNotice(view, locale.locale)),
        action: SnackBarAction(
            label: CommandStrings(locale.locale?.languageCode == 'ar')
                .t('openRunning'),
            onPressed: () =>
                _navigator.currentState?.pushNamed(AnumRoutes.command))));
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
      _messenger.currentState?.showSnackBar(SnackBar(
          content: Text('That notification is for workspace '
              '"${destination.workspaceId}". Switch to it in Settings to open it.')));
      return;
    }
    _navigator.currentState?.pushNamed(destination.route);
  }

  void _showPushNotice(PushNotice notice) {
    final destination = notice.destination;
    _messenger.currentState?.showSnackBar(SnackBar(
        content: Text(notice.body.isEmpty
            ? notice.title
            : '${notice.title}: ${notice.body}'),
        action: destination == null
            ? null
            : SnackBarAction(
                label: 'Open', onPressed: () => _openPush(destination))));
  }

  /// After the person's first approval decision, offer phone push once.
  Future<void> _offerPushAfterApproval() async {
    if (!await push.shouldOfferAfterApproval()) return;
    _messenger.currentState?.showSnackBar(SnackBar(
        duration: const Duration(seconds: 10),
        content: const Text(
            'Get a notification on this phone when an approval is waiting?'),
        action: SnackBarAction(label: 'Turn on', onPressed: push.enable)));
  }

  /// Who is signed in, for the approval cards (one approval per person).
  Future<void> _rememberUser() async {
    try {
      final session = await auth.repository.restoreSession();
      workspace.currentUserId = session?.context.userId;
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
    tyre.dispose();
    push.dispose();
    chatDeveloper.dispose();
    voiceDeveloper.dispose();
    whatsapp.dispose();
    email.dispose();
    command.dispose();
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
              ...GlobalMaterialLocalizations.delegates
            ],
            onGenerateRoute: _onGenerateRoute,
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
                      chatDeveloper: chatDeveloper,
                      whatsappController: whatsapp,
                      emailController: email,
                      commerceController: commerce,
                      workspaceName: auth.workspaceName,
                      onOpenCommand: _openCommand,
                      library: library,
                    )
                  : AuthFlow(controller: auth, intro: intro),
            ),
          ));

  Route<void>? _onGenerateRoute(RouteSettings settings) {
    final name = settings.name;
    if ({
      AnumRoutes.splash,
      AnumRoutes.onboarding,
      AnumRoutes.signIn,
      AnumRoutes.workspaceSetup,
      AnumRoutes.modelSetup
    }.contains(name)) {
      return MaterialPageRoute<void>(
          settings: settings,
          builder: (_) => AuthFlow(controller: auth, intro: intro));
    }
    if (name == AnumRoutes.createAccount) {
      return MaterialPageRoute<void>(
          settings: settings,
          builder: (_) => ListenableBuilder(
              listenable: auth,
              builder: (_, __) => auth.phase == AuthPhase.signedOut
                  ? SignInScreen(controller: auth, startWithRegistration: true)
                  : AuthFlow(controller: auth, intro: intro)));
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
                workspaceName: auth.workspaceName)),
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
    if (name == AnumRoutes.tyres || name == AnumRoutes.tyreAlerts) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
          auth: auth,
          child: TyreScreen(
              controller: tyre,
              initialTab: name == AnumRoutes.tyreAlerts ? 2 : 0),
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
                : QuickCommandsScreen(controller: command)),
      );
    }
    if (name == AnumRoutes.inbox) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(
            auth: auth, child: InboxScreen(controller: email)),
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
        chatDeveloper: chatDeveloper,
        whatsappController: whatsapp,
        emailController: email,
        commerceController: commerce,
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
                body: Center(
                    child: Text('Unable to load task: ${snapshot.error}')));
          }
          final task = snapshot.data;
          return task == null
              ? const Scaffold(body: Center(child: CircularProgressIndicator()))
              : TaskDetailScreen(controller: widget.controller, task: task);
        },
      );
}
