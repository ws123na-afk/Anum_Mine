import 'package:flutter/material.dart';

import '../data/api_client.dart';
import '../features/auth/auth_controller.dart';
import '../features/auth/auth_repository.dart';
import '../features/auth/auth_screens.dart';
import '../features/workspace/api_workspace_repository.dart';
import '../features/workspace/workspace_controller.dart';
import '../features/workspace/workspace_home.dart';
import '../features/voice/speech_service.dart';
import '../features/voice/voice_controller.dart';
import '../features/voice/voice_repository.dart';
import 'infrastructure/mobile_adapters.dart';
import 'theme/anum_theme.dart';

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
    auth = AuthController(AuthRepository(api: api, sessions: sessions));
    workspace = WorkspaceController(ApiWorkspaceRepository(
      api,
      fileTransfer: HttpWorkspaceFileTransfer(
        baseUri: Uri.parse(configuredUrl),
        sessions: sessions,
      ),
    ));
    voice = VoiceController(repository: VoiceRepository(api), speech: DeviceSpeechService());
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
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'ANUM',
        debugShowCheckedModeBanner: false,
        theme: AnumTheme.light(),
        darkTheme: AnumTheme.dark(),
        themeMode: ThemeMode.system,
        home: ListenableBuilder(
          listenable: auth,
          builder: (context, _) => auth.phase == AuthPhase.ready
              ? WorkspaceHome(controller: workspace, voiceController: voice)
              : AuthFlow(controller: auth),
        ),
      );
}
