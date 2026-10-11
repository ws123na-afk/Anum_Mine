// Developer › Projects (docs/developer-mode.md#projects): add, validate,
// test the connection, install the workflow, turn off and delete; requests
// pick a project; chat and voice resolve the project and always confirm it;
// layouts from a 320 px phone at 200 percent text to 1440x900, left to right
// and right to left.
import 'package:anum_mobile/features/developer/developer_assist.dart';
import 'package:anum_mobile/features/developer/developer_assist_panel.dart';
import 'package:anum_mobile/features/developer/developer_controller.dart';
import 'package:anum_mobile/features/developer/developer_models.dart';
import 'package:anum_mobile/features/developer/developer_projects_screen.dart';
import 'package:anum_mobile/features/developer/developer_repository.dart';
import 'package:anum_mobile/features/developer/developer_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'developer_fakes.dart';

Future<void> _pump(
  WidgetTester tester,
  Widget home, {
  // Tall, so the long forms are on screen without scrolling.
  Size size = const Size(390, 2600),
  double textScale = 1,
  TextDirection direction = TextDirection.ltr,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(textScale)),
      child: Directionality(textDirection: direction, child: child!),
    ),
    home: home,
  ));
  await tester.pumpAndSettle();
}

Future<DeveloperController> _loaded(FakeDeveloperApi api) async {
  final c = await buildDeveloper(api);
  await c.ensureOwner();
  await c.load();
  return c;
}

/// Pages build every section at once (a column in a list), so scroll the
/// one on top until [finder] is on screen.
Future<void> _scrollTo(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await _scrollTo(tester, finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'an owner adds a project, tests it, installs the workflow, '
      'turns it off and deletes it', (tester) async {
    final api = FakeDeveloperApi(projects: []);
    final c = await _loaded(api);
    await _pump(tester, DeveloperProjectsScreen(controller: c));
    expect(find.byKey(const Key('developer-projects-empty')), findsOneWidget);
    expect(find.textContaining('ANTHROPIC_API_KEY and ANUM_DEV_PUSH_TOKEN'),
        findsOneWidget);

    await tester.tap(find.byKey(const Key('developer-project-add')));
    await tester.pumpAndSettle();
    expect(find.text('Add a project'), findsWidgets);
    await tester.enterText(
        find.byKey(const Key('developer-project-name')), 'TyrePulse');
    await tester.enterText(
        find.byKey(const Key('developer-project-repository')), 'not a repo');
    await _tap(tester, find.byKey(const Key('developer-project-save')));
    expect(
        find.text(
            'Enter the repository as owner/repo, for example acme/tyrepulse.'),
        findsOneWidget);
    expect(api.calls('POST', '/developer/projects'), isEmpty);

    await _scrollTo(
        tester, find.byKey(const Key('developer-project-repository')));
    await tester.enterText(
        find.byKey(const Key('developer-project-repository')),
        'https://github.com/example/tyrepulse');
    await _scrollTo(
        tester, find.byKey(const Key('developer-project-description')));
    await tester.enterText(
        find.byKey(const Key('developer-project-description')),
        'Tyre pressure monitoring for fleets');
    await _scrollTo(tester, find.byKey(const Key('developer-project-paths')));
    await tester.enterText(
        find.byKey(const Key('developer-project-paths')), 'docs/legal/');
    await _scrollTo(tester, find.byKey(const Key('developer-project-token')));
    await tester.enterText(find.byKey(const Key('developer-project-token')),
        'example-fine-grained-value');
    await _tap(tester, find.byKey(const Key('developer-project-save')));

    final body = api.calls('POST', '/developer/projects').single.body!;
    expect(body['github_owner'], 'example');
    expect(body['github_repo'], 'tyrepulse');
    expect(body['protected_paths'], ['docs/legal/']);
    expect(body['token'], 'example-fine-grained-value');
    // Back on the list: the project, never its token.
    expect(find.text('TyrePulse'), findsOneWidget);
    expect(find.textContaining('example-fine-grained-value'), findsNothing);
    expect(find.text('Not tested'), findsOneWidget);
    expect(find.textContaining('Added TyrePulse.'), findsOneWidget);

    await _tap(tester,
        find.byKey(const Key('developer-project-test-devproj_tyrepulse')));
    expect(find.text('Needs attention'), findsOneWidget);
    expect(find.textContaining('Use Install workflow'), findsWidgets);

    await _tap(tester,
        find.byKey(const Key('developer-project-install-devproj_tyrepulse')));
    expect(find.textContaining('Opened pull request #51 in example/tyrepulse'),
        findsOneWidget);
    expect(find.text('Workflow pull request #51'), findsOneWidget);

    await _tap(tester,
        find.byKey(const Key('developer-project-toggle-devproj_tyrepulse')));
    expect(api.calls('PATCH', '/developer/projects').single.body,
        {'enabled': false});
    expect(find.text('Turned off'), findsOneWidget);

    await _tap(tester,
        find.byKey(const Key('developer-project-delete-devproj_tyrepulse')));
    expect(find.text('Delete TyrePulse?'), findsOneWidget);
    await tester.tap(find.text('Keep it'));
    await tester.pumpAndSettle();
    expect(api.calls('DELETE', '/developer/projects'), isEmpty);
    await _tap(tester,
        find.byKey(const Key('developer-project-delete-devproj_tyrepulse')));
    await tester.tap(find.text('Delete project'));
    await tester.pumpAndSettle();
    expect(api.calls('DELETE', '/developer/projects'), hasLength(1));
    expect(find.byKey(const Key('developer-projects-empty')), findsOneWidget);
  });

  testWidgets('a refused save says why; editing keeps the stored token',
      (tester) async {
    final api = FakeDeveloperApi(projects: [anumProject(), tyreProject()]);
    final c = await _loaded(api);
    await _pump(tester, DeveloperProjectsScreen(controller: c));
    await tester.tap(find.byKey(const Key('developer-project-add')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('developer-project-name')), 'anum');
    await tester.enterText(
        find.byKey(const Key('developer-project-repository')), 'example/other');
    await _tap(tester, find.byKey(const Key('developer-project-save')));
    expect(find.byKey(const Key('developer-project-error')), findsOneWidget);
    expect(find.textContaining('Another project already has this name'),
        findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();

    await _tap(
        tester, find.byKey(const Key('developer-project-edit-devproj_tyre')));
    expect(find.text('Edit TyrePulse'), findsOneWidget);
    expect(find.textContaining('A token is stored'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('developer-project-branch')), 'develop');
    await _tap(tester, find.byKey(const Key('developer-project-save')));
    final patch = api.calls('PATCH', '/developer/projects').single.body!;
    expect(patch['default_branch'], 'develop');
    expect(patch.containsKey('token'), isFalse);
    expect(patch.containsKey('clear_token'), isFalse);
    expect(find.textContaining('Saved TyrePulse.'), findsOneWidget);
  });

  testWidgets('a request goes to the chosen project', (tester) async {
    final api = FakeDeveloperApi(projects: [anumProject(), tyreProject()]);
    final c = await buildDeveloper(api);
    await _pump(tester, Scaffold(body: DeveloperScreen(controller: c)));
    expect(find.byKey(const Key('developer-projects-row')), findsOneWidget);
    expect(find.text('2 projects, 2 ready'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('developer-draft')),
        'Add a tread depth chart to the fleet view');
    await tester.pump();
    final send = find.byKey(const Key('developer-send'));
    expect(tester.widget<FilledButton>(send).onPressed, isNull,
        reason: 'no project chosen yet');

    await tester.tap(find.byKey(const Key('developer-project')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('TyrePulse (example/tyrepulse)').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('builds example/tyrepulse from main'),
        findsOneWidget);
    await _tap(tester, send);
    final body = api.calls('POST', '/developer/requests').single.body!;
    expect(body['project_id'], 'devproj_tyre');
    expect(find.textContaining('Sent to the developer for TyrePulse'),
        findsOneWidget);
    expect(find.textContaining('TyrePulse · '), findsOneWidget);
  });

  testWidgets('without a ready project the screen leads to Projects',
      (tester) async {
    final api = FakeDeveloperApi(projects: []);
    final c = await buildDeveloper(api);
    await _pump(tester, Scaffold(body: DeveloperScreen(controller: c)));
    expect(find.text('Add a project first'), findsOneWidget);
    expect(find.byKey(const Key('developer-draft')), findsNothing);
    await _tap(tester, find.text('Add a project'));
    expect(find.byType(DeveloperProjectsScreen), findsOneWidget);
  });

  group('chat and voice', () {
    Future<DeveloperAssist> assistFor(FakeDeveloperApi api) async =>
        DeveloperAssist(
          repository: ApiDeveloperRepository(await developerApi(api)),
          source: 'voice',
          isOwner: () async => true,
        );

    test('a named project is confirmed; the request goes to it', () async {
      final api = FakeDeveloperApi(projects: [anumProject(), tyreProject()]);
      final assist = await assistFor(api);
      expect(await assist.intercept('build a login page in TyrePulse'),
          "I'll ask the developer to: a login page in TyrePulse. Start?");
      expect(assist.pendingProject!.id, 'devproj_tyre');
      expect(api.calls('POST', '/developer/requests'), isEmpty);
      expect(
          await assist.intercept('yes'), startsWith('Started in TyrePulse.'));
      final body = api.calls('POST', '/developer/requests').single.body!;
      expect(body['project_id'], 'devproj_tyre');
      expect(body['text'], 'a login page');
    });

    test('an unclear request asks which project; a spoken name picks it',
        () async {
      final api = FakeDeveloperApi(projects: [anumProject(), tyreProject()]);
      final assist = await assistFor(api);
      expect(await assist.intercept('developer, add a dark mode toggle'),
          'Which project is this for: ANUM or TyrePulse?');
      expect(assist.choosing, isTrue);
      expect(assist.awaitingAnswer, isTrue);
      expect(await assist.intercept('hmm'),
          "I didn't catch the project. Say ANUM or TyrePulse, or cancel.");
      expect(
          await assist.intercept('the tyre pulse one'),
          "I'll ask the developer to: add a dark mode toggle in TyrePulse. "
          'Start?');
      expect(await assist.intercept('no'), "Okay, I won't start it.");
      expect(api.calls('POST', '/developer/requests'), isEmpty);
    });

    test('no ready project says where to add one', () async {
      final api = FakeDeveloperApi(projects: []);
      final assist = await assistFor(api);
      expect(await assist.intercept('build a weekly digest email'),
          noProjectsReply);
      expect(assist.awaitingAnswer, isFalse);
    });

    testWidgets('the panel offers the projects as buttons, then Start',
        (tester) async {
      final api = FakeDeveloperApi(projects: [anumProject(), tyreProject()]);
      final assist = await assistFor(api);
      await assist.intercept('build a dark mode toggle for settings');
      await _pump(
          tester,
          Scaffold(
              body: ListenableBuilder(
                  listenable: assist,
                  builder: (context, _) =>
                      DeveloperAssistPanel(assist: assist))));
      expect(find.byKey(const Key('developer-which-project')), findsOneWidget);
      await tester.tap(find.byKey(const Key('developer-choose-devproj_anum')));
      await tester.pumpAndSettle();
      expect(
          find.text("I'll ask the developer to: a dark mode toggle for "
              'settings in ANUM. Start?'),
          findsOneWidget);
      expect(find.text('example/anum'), findsOneWidget);
      await tester.tap(find.byKey(const Key('developer-start')));
      await tester.pumpAndSettle();
      expect(
          api.calls('POST', '/developer/requests').single.body!['project_id'],
          'devproj_anum');
    });
  });

  group('layout', () {
    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      testWidgets('Projects and the form fit ${width.toInt()} px at 200% text',
          (tester) async {
        final api = FakeDeveloperApi(projects: [
          anumProject(),
          projectJson('devproj_long', 'A project with a rather long name',
              repository: 'an-organization-name/a-repository-with-a-long-name',
              description: 'Fleet tyres, pressure alerts and tread depth for '
                  'every vehicle in the depot',
              protectedPaths: ['docs/legal/', 'src/billing/'],
              installPr: 51,
              lastCheck: {
                'ok': false,
                'checked_at': '2026-10-11T10:00:00Z',
                'message': '.github/workflows/anum-developer.yml is not on '
                    'main yet.',
              }),
        ]);
        final c = await _loaded(api);
        await _pump(tester, DeveloperProjectsScreen(controller: c),
            size: Size(width, 4000), textScale: 2);
        expect(tester.takeException(), isNull);
        await _scrollTo(tester,
            find.byKey(const Key('developer-project-delete-devproj_long')));
        expect(tester.takeException(), isNull);
        await _tap(tester, find.byKey(const Key('developer-project-add')));
        await tester.pumpAndSettle();
        await _scrollTo(
            tester, find.byKey(const Key('developer-project-save')));
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('right to left, the project card and request form fit 390 px',
        (tester) async {
      final api = FakeDeveloperApi(projects: [anumProject(), tyreProject()]);
      final c = await _loaded(api);
      await _pump(tester, DeveloperProjectsScreen(controller: c),
          direction: TextDirection.rtl);
      expect(tester.takeException(), isNull);
      expect(find.text('example/anum · main'), findsOneWidget);
      final c2 = await buildDeveloper(api);
      await _pump(tester, Scaffold(body: DeveloperScreen(controller: c2)),
          direction: TextDirection.rtl, textScale: 2);
      expect(tester.takeException(), isNull);
      expect(find.byKey(const Key('developer-project')), findsOneWidget);
    });
  });
}
