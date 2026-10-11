// The Assistant is the workspace's first destination, and system Back closes
// its conversation list on a phone (PopScope in WorkspaceHome).
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/chat/chat_controller.dart';
import 'package:anum_mobile/features/chat/chat_repository.dart';
import 'package:anum_mobile/features/governance/api_governance_repository.dart';
import 'package:anum_mobile/features/governance/governance_controller.dart';
import 'package:anum_mobile/features/settings/settings_controller.dart';
import 'package:anum_mobile/features/voice/voice_controller.dart';
import 'package:anum_mobile/features/voice/voice_preferences.dart';
import 'package:anum_mobile/features/voice/voice_repository.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_home.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import '../voice/voice_fakes.dart';
import 'chat_fakes.dart';

/// Conversations from the fake; every other list is empty.
class _WorkspaceTransport implements ApiTransport {
  _WorkspaceTransport(this.conversations);
  final FakeConversationsApi conversations;
  @override
  Future<ApiResponse> send(ApiRequest request) async =>
      request.uri.path.startsWith('/api/v1/conversations')
          ? conversations.send(request)
          : const ApiResponse(statusCode: 200, body: {'data': <Object?>[]});
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

class Shell {
  Shell(this.home, this.chat, this.voice);
  final Widget home;
  final ChatController chat;
  final VoiceController voice;
}

Future<Shell> buildShell(FakeConversationsApi conversations,
    {ScriptedEvents? events}) async {
  final sessions = MemorySessionStore();
  await sessions.write((await (await signedInSessions()).read())!);
  final api = AnumApiClient(
      baseUri: Uri.parse('https://anum.test'),
      transport: _WorkspaceTransport(conversations),
      sessions: sessions);
  final chat = ChatController(ChatRepository(api, events ?? ScriptedEvents()));
  final voice = VoiceController(
      repository: VoiceRepository(api),
      speech: FakeSpeech(),
      preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'}),
      restartDelay: Duration.zero);
  final home = WorkspaceHome(
    controller: WorkspaceController(
        ApiWorkspaceRepository(api, fileTransfer: _NoFiles())),
    chatController: chat,
    voiceController: voice,
    settingsController:
        SettingsController(AuthRepository(api: api, sessions: sessions)),
    governanceController: GovernanceController(
        ApiGovernanceRepository(api, auditExporter: _NoExport())),
    localeController: LocaleController(),
    workspaceName: 'Operations',
  );
  return Shell(home, chat, voice);
}

Future<void> pumpShell(WidgetTester tester, Widget home,
    {Size size = const Size(390, 844)}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(disableAnimations: true),
      child: child!,
    ),
    home: home,
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('on a phone the Assistant is the first, selected destination',
      (tester) async {
    final shell = await tester.runAsync(() => buildShell(FakeConversationsApi(
        conversations: [conversation('conv_1', 'Quarterly plan')])));
    await pumpShell(tester, shell!.home);

    final bar = tester.widget<NavigationBar>(find.byType(NavigationBar));
    expect(bar.selectedIndex, 0);
    expect(
        (bar.destinations.first as NavigationDestination).label, 'Assistant');
    expect(find.text('Quarterly plan'), findsOneWidget);
    expect(find.byKey(const Key('chat-input')), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.byIcon(Icons.home_outlined));
    await tester.pumpAndSettle();
    expect(
        tester.widget<NavigationBar>(find.byType(NavigationBar)).selectedIndex,
        1);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('system Back closes the conversation list, not the workspace',
      (tester) async {
    final shell = await tester.runAsync(() => buildShell(FakeConversationsApi(
        conversations: [conversation('conv_1', 'Quarterly plan')])));
    await pumpShell(tester, shell!.home);

    await tester.tap(find.byTooltip('Conversations (1)'));
    await tester.pumpAndSettle();
    expect(shell.chat.listOpen.value, isTrue);
    expect(find.byKey(const Key('chat-conversations')), findsOneWidget);

    final handled = await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(handled, isTrue);
    expect(shell.chat.listOpen.value, isFalse);
    expect(find.byKey(const Key('chat-input')), findsOneWidget);
    expect(find.byType(WorkspaceHome), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
      'on a tablet the rail starts with the Assistant, list beside the chat',
      (tester) async {
    final shell = await tester.runAsync(() => buildShell(FakeConversationsApi(
        conversations: [conversation('conv_1', 'Quarterly plan')])));
    await pumpShell(tester, shell!.home, size: const Size(1366, 768));
    final rail = tester.widget<NavigationRail>(find.byType(NavigationRail));
    expect(rail.selectedIndex, 0);
    expect(find.text('Conversations'), findsOneWidget);
    expect(find.byKey(const Key('chat-input')), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
}
