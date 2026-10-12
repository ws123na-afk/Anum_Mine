// The Menu drawer reaches Favorites, Reports and the Knowledge Base
// (docs/knowledge-base.md#clients); the Knowledge Base keeps memories and
// files one tap away.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/chat/chat_controller.dart';
import 'package:anum_mobile/features/chat/chat_repository.dart';
import 'package:anum_mobile/features/governance/api_governance_repository.dart';
import 'package:anum_mobile/features/governance/governance_controller.dart';
import 'package:anum_mobile/features/library/library_screens.dart';
import 'package:anum_mobile/features/settings/settings_controller.dart';
import 'package:anum_mobile/features/voice/voice_controller.dart';
import 'package:anum_mobile/features/voice/voice_preferences.dart';
import 'package:anum_mobile/features/voice/voice_repository.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/destinations.dart';
import 'package:anum_mobile/features/workspace/resources_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_home.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'chat/assistant_navigation_test.dart' show settleByTime;
import 'chat/chat_fakes.dart';
import 'library_test.dart' show FakeLibrary;
import 'voice/voice_fakes.dart';

class _Empty implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) async =>
      const ApiResponse(statusCode: 200, body: {'data': <Object?>[]});
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}

class _NoExport implements AuditExporter {
  @override
  Future<void> export(String format) async {}
}

Future<void> _pumpHome(WidgetTester tester,
    {Locale locale = const Locale('en')}) async {
  final sessions = MemorySessionStore();
  await sessions.write((await (await signedInSessions()).read())!);
  final api = AnumApiClient(
      baseUri: Uri.parse('https://anum.test'),
      transport: _Empty(),
      sessions: sessions);
  final home = WorkspaceHome(
    controller: WorkspaceController(
        ApiWorkspaceRepository(api, fileTransfer: _NoFiles())),
    chatController: ChatController(ChatRepository(api, ScriptedEvents())),
    voiceController: VoiceController(
        repository: VoiceRepository(api),
        speech: FakeSpeech(),
        preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'}),
        restartDelay: Duration.zero),
    settingsController:
        SettingsController(AuthRepository(api: api, sessions: sessions)),
    governanceController: GovernanceController(
        ApiGovernanceRepository(api, auditExporter: _NoExport())),
    localeController: LocaleController(),
    workspaceName: 'Operations',
    library: FakeLibrary(),
    initialIndex: WorkspaceDestination.home,
  );
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(disableAnimations: true),
        child: child!),
    home: home,
  ));
  await settleByTime(tester);
}

Future<void> _openMenu(WidgetTester tester) async {
  final state = tester.firstState<ScaffoldState>(find.byType(Scaffold));
  state.openDrawer();
  await settleByTime(tester);
}

Future<void> _tapEntry(WidgetTester tester, String key) async {
  final entry = find.byKey(Key('menu-$key'));
  await tester.ensureVisible(entry);
  await tester.tap(entry);
  await settleByTime(tester);
}

void main() {
  testWidgets('the menu opens Favorites, Reports and the Knowledge Base',
      (tester) async {
    await _pumpHome(tester);
    await _openMenu(tester);
    await _tapEntry(tester, 'libFavorites');
    expect(find.byType(FavoritesScreen), findsOneWidget);
    await tester.pageBack();
    await settleByTime(tester);

    await _openMenu(tester);
    await _tapEntry(tester, 'libReports');
    expect(find.byType(ReportsScreen), findsOneWidget);
    await tester.pageBack();
    await settleByTime(tester);

    await _openMenu(tester);
    await _tapEntry(tester, 'shellKnowledge');
    expect(find.byType(KnowledgeScreen), findsOneWidget);
    // Memories and files stay one tap away: back to Resources.
    await tester.tap(find.byKey(const Key('kb-memories')));
    await settleByTime(tester);
    expect(find.byType(KnowledgeScreen), findsNothing);
    expect(find.byType(ResourcesScreen), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Arabic: the menu entries are translated', (tester) async {
    await _pumpHome(tester, locale: const Locale('ar'));
    await _openMenu(tester);
    expect(find.text('المفضلة'), findsWidgets);
    expect(find.text('التقارير'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
