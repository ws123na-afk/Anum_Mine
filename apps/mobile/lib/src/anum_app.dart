import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import '../data/api_client.dart';
import '../data/session_store.dart';
import '../features/auth/auth_controller.dart';
import '../features/auth/auth_repository.dart';
import '../features/auth/auth_screens.dart';
import '../features/auth/oidc.dart';
import '../features/chat/chat_controller.dart';
import '../features/chat/chat_repository.dart';
import '../features/workspace/api_workspace_repository.dart';
import '../features/workspace/destinations.dart';
import '../features/workspace/workspace_controller.dart';
import '../features/workspace/workspace_home.dart';
import '../features/workspace/tasks_screen.dart';
import '../features/voice/speech_service.dart';
import '../features/voice/voice_controller.dart';
import '../features/voice/voice_repository.dart';
import '../features/settings/settings_controller.dart';
import '../features/admin/admin_repository.dart';
import '../features/governance/api_governance_repository.dart';
import '../features/governance/governance_controller.dart';
import 'infrastructure/appauth_authenticator.dart';
import 'infrastructure/http_event_stream.dart';
import 'infrastructure/mobile_adapters.dart';
import 'theme/anum_theme.dart';
import 'localization/anum_localizations.dart';

abstract final class AnumRoutes {
  static const splash = '/splash';
  static const signIn = '/sign-in';
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
  late final ChatController chat;
  late final VoiceController voice;
  late final SettingsController settings;
  late final GovernanceController governance;
  late final LocaleController locale;

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
    final authRepository = AuthRepository(
        api: api,
        sessions: sessions,
        oidc: oidc,
        workspacePreferences: SecureWorkspacePreferenceStore());
    auth = AuthController(authRepository);
    workspace = WorkspaceController(ApiWorkspaceRepository(
      api,
      fileTransfer: HttpWorkspaceFileTransfer(
        baseUri: Uri.parse(configuredUrl),
        sessions: sessions,
      ),
    ));
    // Streamed replies for the Assistant and voice (docs/conversations.md).
    final events = HttpEventStreamClient(
        baseUri: Uri.parse(configuredUrl), sessions: sessions);
    chat = ChatController(ChatRepository(api, events));
    voice = VoiceController(
        repository: VoiceRepository(api, events: events),
        speech: DeviceSpeechService());
    settings =
        SettingsController(authRepository, admin: ApiAdminRepository(api));
    governance = GovernanceController(ApiGovernanceRepository(
      api,
      auditExporter: HttpAuditExporter(
          baseUri: Uri.parse(configuredUrl), sessions: sessions),
    ));
    locale = LocaleController()..restore();
    auth.addListener(_onAuthChanged);
    auth.restore();
  }

  void _onAuthChanged() {
    if (auth.phase == AuthPhase.ready && workspace.phase.name == 'initial') {
      workspace.load();
    }
    if (auth.phase == AuthPhase.signedOut) {
      workspace.currentUserId = null;
      // The next person to sign in never sees these conversations.
      chat.reset();
    }
    if (auth.phase == AuthPhase.ready && workspace.currentUserId == null) {
      _rememberUser();
    }
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
    chat.dispose();
    voice.dispose();
    settings.dispose();
    governance.dispose();
    locale.dispose();
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
      listenable: locale,
      builder: (context, _) => MaterialApp(
            title: 'ANUM',
            debugShowCheckedModeBanner: false,
            theme: AnumTheme.light(),
            darkTheme: AnumTheme.dark(),
            themeMode: ThemeMode.dark,
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
                      workspaceName: auth.workspaceName,
                    )
                  : AuthFlow(controller: auth),
            ),
          ));

  Route<void>? _onGenerateRoute(RouteSettings settings) {
    final name = settings.name;
    if ({
      AnumRoutes.splash,
      AnumRoutes.signIn,
      AnumRoutes.workspaceSetup,
      AnumRoutes.modelSetup
    }.contains(name)) {
      return MaterialPageRoute<void>(
          settings: settings, builder: (_) => AuthFlow(controller: auth));
    }
    final index = switch (name) {
      AnumRoutes.assistant => WorkspaceDestination.assistant,
      AnumRoutes.home => WorkspaceDestination.home,
      AnumRoutes.tasks => WorkspaceDestination.tasks,
      AnumRoutes.voice => WorkspaceDestination.voice,
      AnumRoutes.approvals => WorkspaceDestination.approvals,
      AnumRoutes.automations => WorkspaceDestination.automations,
      AnumRoutes.files => WorkspaceDestination.resources,
      _ => null,
    };
    if (index != null) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) =>
            _AuthenticatedDestination(auth: auth, child: _workspace(index)),
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
        workspaceName: auth.workspaceName,
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
