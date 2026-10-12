// The Assistant is Chat in the phone's bottom bar and first in the rail, and
// system Back closes its conversation list on a phone (PopScope in
// WorkspaceHome).
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
import 'package:anum_mobile/features/workspace/destinations.dart';
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
    {ScriptedEvents? events,
    int initialIndex = WorkspaceDestination.assistant}) async {
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
    initialIndex: initialIndex,
  );
  return Shell(home, chat, voice);
}

Future<void> pumpShell(WidgetTester tester, Widget home,
    {Size size = const Size(390, 844),
    bool settle = true,
    Locale locale = const Locale('en'),
    double scale = 1}) async {
  tester.view.physicalSize = size;
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
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(scale)),
      child: child!,
    ),
    home: home,
  ));
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await settleByTime(tester);
  }
}

/// The Assistant's list keeps a spinner while it is off screen, so settle
/// by time instead.
Future<void> settleByTime(WidgetTester tester) async {
  for (var i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

void main() {
  testWidgets('on a phone Home opens first and Chat is the Assistant',
      (tester) async {
    final shell = await tester.runAsync(() => buildShell(
        FakeConversationsApi(
            conversations: [conversation('conv_1', 'Quarterly plan')]),
        initialIndex: WorkspaceDestination.home));
    await pumpShell(tester, shell!.home, settle: false);

    expect(find.byType(NavigationBar), findsNothing);
    expect(find.byKey(const Key('nav-chat')), findsOneWidget);
    expect(find.byKey(const Key('header-bell')), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.byKey(const Key('nav-chat')));
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
    expect(find.text('Quarterly plan'), findsOneWidget);
    expect(find.byKey(const Key('chat-input')), findsOneWidget);
    expect(tester.takeException(), isNull);
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

  testWidgets('the Menu reaches every destination; the bell opens Approvals',
      (tester) async {
    final shell = await tester.runAsync(() => buildShell(
        FakeConversationsApi(conversations: const []),
        initialIndex: WorkspaceDestination.home));
    await pumpShell(tester, shell!.home, settle: false);

    await tester.tap(find.byKey(const Key('nav-menu')));
    await settleByTime(tester);
    for (final label in [
      'Home',
      'Agents',
      'Tasks',
      'Automations',
      'Integrations',
      'Knowledge Base',
      'Calls & Meetings',
      'Settings',
      'Approvals',
    ]) {
      expect(find.text(label), findsWidgets, reason: label);
    }
    await tester.scrollUntilVisible(find.byKey(const Key('menu-plan')), 200,
        scrollable: find.descendant(
            of: find.byType(Drawer), matching: find.byType(Scrollable)));
    expect(find.text('Free plan'), findsOneWidget);
    await tester.tap(find.byKey(const Key('menu-shellAutomations')));
    await settleByTime(tester);
    expect(find.byType(Drawer), findsNothing);

    await tester.tap(find.byKey(const Key('nav-home')));
    await settleByTime(tester);
    await tester.tap(find.byKey(const Key('header-bell')));
    await settleByTime(tester);
    expect(find.text('Approvals'), findsWidgets);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('the orb opens the command surface; Arabic RTL at 200% text',
      (tester) async {
    final shell = await tester.runAsync(() => buildShell(
        FakeConversationsApi(conversations: const []),
        initialIndex: WorkspaceDestination.home));
    await pumpShell(tester, shell!.home,
        settle: false,
        size: const Size(320, 700),
        locale: const Locale('ar'),
        scale: 2);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byKey(const Key('nav-orb')));
    await settleByTime(tester);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  for (final size in const [Size(1024, 768), Size(1440, 900)]) {
    testWidgets('wide ${size.width}: rail with orb and menu, header on top',
        (tester) async {
      final shell = await tester.runAsync(() => buildShell(
          FakeConversationsApi(conversations: const []),
          initialIndex: WorkspaceDestination.home));
      await pumpShell(tester, shell!.home, settle: false, size: size);
      expect(find.byType(NavigationRail), findsOneWidget);
      expect(find.byKey(const Key('rail-orb')), findsOneWidget);
      expect(find.byKey(const Key('header-bell')), findsOneWidget);
      await tester.tap(find.byKey(const Key('rail-menu')));
      await settleByTime(tester);
      expect(find.byType(Drawer), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
