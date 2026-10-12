// Developer in the workspace navigation: listed for owners only (rail and
// the phone's Menu drawer); a deep link for anyone else explains it.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/chat/chat_controller.dart';
import 'package:anum_mobile/features/chat/chat_repository.dart';
import 'package:anum_mobile/features/developer/developer_controller.dart';
import 'package:anum_mobile/features/developer/developer_repository.dart';
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
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import '../chat/chat_fakes.dart';
import '../voice/voice_fakes.dart';
import 'developer_fakes.dart';

/// Developer and membership routes from the fake; every other list is empty.
class _Transport implements ApiTransport {
  _Transport(this.developer);
  final FakeDeveloperApi developer;
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final path = request.uri.path;
    if (path.startsWith('/api/v1/developer') ||
        path == '/api/v1/workspace-memberships/current') {
      return developer.send(request);
    }
    return const ApiResponse(statusCode: 200, body: {'data': <Object?>[]});
  }
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

Future<WorkspaceHome> _home(FakeDeveloperApi developer,
    {int initialIndex = 0}) async {
  final api = await developerApi(_Transport(developer));
  return WorkspaceHome(
    controller: WorkspaceController(
        ApiWorkspaceRepository(api, fileTransfer: _NoFiles())),
    chatController: ChatController(ChatRepository(api, ScriptedEvents())),
    voiceController: VoiceController(
        repository: VoiceRepository(api),
        speech: FakeSpeech(),
        preferences: MemoryVoicePreferences({'anum.voice.wake': 'off'}),
        restartDelay: Duration.zero),
    settingsController:
        SettingsController(AuthRepository(api: api, sessions: api.sessions)),
    governanceController: GovernanceController(
        ApiGovernanceRepository(api, auditExporter: _NoExport())),
    localeController: LocaleController(),
    developerController: DeveloperController(ApiDeveloperRepository(api)),
    workspaceName: 'Operations',
    initialIndex: initialIndex,
  );
}

Future<void> _pump(WidgetTester tester, Widget home, Size size,
    {Locale locale = const Locale('en')}) async {
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
      data: MediaQuery.of(context).copyWith(disableAnimations: true),
      child: child!,
    ),
    home: home,
  ));
  await _settle(tester);
}

/// A destination that is not on screen (the Assistant's list before it
/// first opens) keeps its spinner running, so settle by time instead.
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

List<String> _railLabels(WidgetTester tester) => tester
    .widget<NavigationRail>(find.byType(NavigationRail))
    .destinations
    .map((d) => (d.label as Text).data!)
    .toList();

void main() {
  testWidgets('owners get Developer last in the rail and can open it',
      (tester) async {
    final developer = FakeDeveloperApi(requests: [requestJson('dev_1')]);
    final home = await tester.runAsync(() => _home(developer));
    await _pump(tester, home!, const Size(1440, 900));
    expect(_railLabels(tester).last, 'Developer');
    expect(_railLabels(tester), hasLength(8));

    await tester.tap(find.text('Developer'));
    await _settle(tester);
    expect(
        tester
            .widget<NavigationRail>(find.byType(NavigationRail))
            .selectedIndex,
        WorkspaceDestination.developer);
    expect(find.text('Ask for a change'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('members never see Developer in the navigation', (tester) async {
    final developer = FakeDeveloperApi(role: 'member');
    final home = await tester.runAsync(() => _home(developer));
    await _pump(tester, home!, const Size(1440, 900));
    expect(_railLabels(tester), isNot(contains('Developer')));
    expect(_railLabels(tester), hasLength(7));
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a deep link for a member explains who can use it',
      (tester) async {
    final developer = FakeDeveloperApi(role: 'viewer');
    final home = await tester.runAsync(
        () => _home(developer, initialIndex: WorkspaceDestination.developer));
    await _pump(tester, home!, const Size(1024, 768));
    expect(find.text('Developer mode is for workspace owners'), findsOneWidget);
    expect(
        tester
            .widget<NavigationRail>(find.byType(NavigationRail))
            .selectedIndex,
        isNull);
    expect(developer.calls('GET', '/developer/'), isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('on a phone owners find Developer in the Menu', (tester) async {
    final developer = FakeDeveloperApi();
    final home = await tester.runAsync(() => _home(developer));
    await _pump(tester, home!, const Size(390, 844));
    await tester.tap(find.byKey(const Key('nav-menu')));
    await _settle(tester);
    await tester.tap(find.text('Developer'));
    await _settle(tester);
    expect(find.text('No developer requests yet'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('the Menu has no Developer for members; Arabic label',
      (tester) async {
    final member =
        await tester.runAsync(() => _home(FakeDeveloperApi(role: 'member')));
    await _pump(tester, member!, const Size(390, 844));
    await tester.tap(find.byKey(const Key('nav-menu')));
    await _settle(tester);
    expect(find.text('Developer'), findsNothing);
    await tester.pumpWidget(const SizedBox());

    final owner = await tester.runAsync(() => _home(FakeDeveloperApi()));
    await _pump(tester, owner!, const Size(1440, 900),
        locale: const Locale('ar'));
    expect(_railLabels(tester).last, 'المطوّر');
    await tester.pumpWidget(const SizedBox());
  });
}
