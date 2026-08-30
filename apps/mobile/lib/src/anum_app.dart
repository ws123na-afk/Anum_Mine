import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import '../data/api_client.dart';
import '../features/auth/auth_controller.dart';
import '../features/auth/auth_repository.dart';
import '../features/auth/auth_screens.dart';
import '../features/workspace/api_workspace_repository.dart';
import '../features/workspace/workspace_controller.dart';
import '../features/workspace/workspace_home.dart';
import '../features/workspace/workspace_screens.dart';
import '../features/voice/speech_service.dart';
import '../features/voice/voice_controller.dart';
import '../features/voice/voice_repository.dart';
import '../features/settings/settings_controller.dart';
import '../features/governance/api_governance_repository.dart';
import '../features/governance/governance_controller.dart';
import 'infrastructure/mobile_adapters.dart';
import 'theme/anum_theme.dart';
import 'localization/anum_localizations.dart';

abstract final class AnumRoutes {
  static const splash = '/splash';
  static const signIn = '/sign-in';
  static const workspaceSetup = '/workspace-setup';
  static const modelSetup = '/model-setup';
  static const home = '/home';
  static const tasks = '/tasks';
  static const taskDetail = '/tasks/:id';
  static const approvals = '/approvals';
  static const automations = '/automations';
  static const files = '/files';
  static const voice = '/voice';
}

class AnumApp extends StatefulWidget {
  const AnumApp({super.key});

  @override
  State<AnumApp> createState() => _AnumAppState();
}

class _AnumAppState extends State<AnumApp> {
  late final AuthController auth;
  late final WorkspaceController workspace;
  late final VoiceController voice;
  late final SettingsController settings;
  late final GovernanceController governance;
  late final LocaleController locale;

  @override
  void initState() {
    super.initState();
    const configuredUrl = String.fromEnvironment(
      'ANUM_API_URL',
      defaultValue: 'http://10.0.2.2:8000/',
    );
    final sessions = SecureSessionStore();
    final api = AnumApiClient(
      baseUri: Uri.parse(configuredUrl),
      transport: HttpApiTransport(),
      sessions: sessions,
    );
    final authRepository = AuthRepository(api: api, sessions: sessions);
    auth = AuthController(authRepository);
    workspace = WorkspaceController(ApiWorkspaceRepository(
      api,
      fileTransfer: HttpWorkspaceFileTransfer(
        baseUri: Uri.parse(configuredUrl),
        sessions: sessions,
      ),
    ));
    voice = VoiceController(repository: VoiceRepository(api), speech: DeviceSpeechService());
    settings = SettingsController(authRepository);
    governance = GovernanceController(ApiGovernanceRepository(
      api,
      auditExporter: HttpAuditExporter(baseUri: Uri.parse(configuredUrl), sessions: sessions),
    ));
    locale = LocaleController()..restore();
    auth.addListener(_onAuthChanged);
    auth.restore();
  }

  void _onAuthChanged() {
    if (auth.phase == AuthPhase.ready && workspace.phase.name == 'initial') {
      workspace.load();
    }
  }

  @override
  void dispose() {
    auth.removeListener(_onAuthChanged);
    auth.dispose();
    workspace.dispose();
    voice.dispose();
    settings.dispose();
    governance.dispose();
    locale.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(listenable: locale, builder: (context, _) => MaterialApp(
        title: 'ANUM',
        debugShowCheckedModeBanner: false,
        theme: AnumTheme.light(),
        darkTheme: AnumTheme.dark(),
        themeMode: ThemeMode.system,
        locale: locale.locale,
        restorationScopeId: 'anum_mobile',
        supportedLocales: const [Locale('en'), Locale('ar')],
        localizationsDelegates: const [AnumLocalizations.delegate, ...GlobalMaterialLocalizations.delegates],
        onGenerateRoute: _onGenerateRoute,
        home: ListenableBuilder(
          listenable: auth,
          builder: (context, _) => auth.phase == AuthPhase.ready
              ? WorkspaceHome(
                  controller: workspace,
                  voiceController: voice,
                  settingsController: settings,
                  governanceController: governance,
                  localeController: locale,
                )
              : AuthFlow(controller: auth),
        ),
      ));

  Route<void>? _onGenerateRoute(RouteSettings settings) {
    final name = settings.name;
    if ({AnumRoutes.splash, AnumRoutes.signIn, AnumRoutes.workspaceSetup, AnumRoutes.modelSetup}.contains(name)) {
      return MaterialPageRoute<void>(settings: settings, builder: (_) => AuthFlow(controller: auth));
    }
    final index = switch (name) {
      AnumRoutes.home => 0,
      AnumRoutes.tasks => 1,
      AnumRoutes.voice => 2,
      AnumRoutes.approvals => 3,
      AnumRoutes.automations => 4,
      AnumRoutes.files => 5,
      _ => null,
    };
    if (index != null) {
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => _AuthenticatedDestination(auth: auth, child: _workspace(index)),
      );
    }
    if (name != null && name.startsWith('/tasks/') && name.length > '/tasks/'.length) {
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
        voiceController: voice,
        settingsController: settings,
        governanceController: governance,
        localeController: locale,
        initialIndex: index,
      );
}

class _AuthenticatedDestination extends StatelessWidget {
  const _AuthenticatedDestination({required this.auth, required this.child});
  final AuthController auth;
  final Widget child;
  @override Widget build(BuildContext context) => ListenableBuilder(
        listenable: auth,
        builder: (_, __) => auth.phase == AuthPhase.ready ? child : AuthFlow(controller: auth),
      );
}

class _TaskDeepLink extends StatefulWidget {
  const _TaskDeepLink({required this.controller, required this.taskId});
  final WorkspaceController controller;
  final String taskId;
  @override State<_TaskDeepLink> createState() => _TaskDeepLinkState();
}

class _TaskDeepLinkState extends State<_TaskDeepLink> {
  late final future = widget.controller.loadTask(widget.taskId);
  @override Widget build(BuildContext context) => FutureBuilder(
        future: future,
        builder: (context, snapshot) {
          if (snapshot.hasError) return Scaffold(appBar: AppBar(title: const Text('Task detail')), body: Center(child: Text('Unable to load task: ${snapshot.error}')));
          final task = snapshot.data;
          return task == null
              ? const Scaffold(body: Center(child: CircularProgressIndicator()))
              : TaskDetailScreen(controller: widget.controller, task: task);
        },
      );
}
