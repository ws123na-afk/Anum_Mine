// The Developer screen (docs/developer-mode.md, Clients): every state, the
// request detail with its timeline and links, Cancel and the payload-hash
// merge, and layouts from a 320 px phone at 200 percent text to 1440x900.
import 'dart:async';

import 'package:anum_mobile/features/developer/developer_controller.dart';
import 'package:anum_mobile/features/developer/developer_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'developer_fakes.dart';

class _Links {
  final opened = <Uri>[];
  bool works = true;
  Future<bool> open(Uri uri) async {
    opened.add(uri);
    return works;
  }
}

Future<void> pumpDeveloper(
  WidgetTester tester,
  DeveloperController controller, {
  Size size = const Size(390, 844),
  double textScale = 1,
  TextDirection direction = TextDirection.ltr,
  LinkOpener? openLink,
  VoidCallback? onOpenSettings,
  bool settle = true,
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
    home: Scaffold(
      body: DeveloperScreen(
        controller: controller,
        openLink: openLink ?? _Links().open,
        onOpenSettings: onOpenSettings,
      ),
    ),
  ));
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
    await tester.pump();
  }
}

/// Scrolls the page on top (the list, or the detail over it) to [finder].
Future<void> _scrollTo(WidgetTester tester, Finder finder) async {
  await tester.scrollUntilVisible(finder, 200,
      scrollable: find
          .descendant(
              of: find.byType(ListView).last, matching: find.byType(Scrollable))
          .first);
  await tester.pumpAndSettle();
}

Future<void> _open(WidgetTester tester, String id) async {
  final tile = find.byKey(Key('developer-request-$id'));
  await _scrollTo(tester, tile);
  await tester.tap(tile);
  await tester.pumpAndSettle();
}

List<Map<String, Object?>> _mixed() => [
      requestJson('dev_3',
          text: 'Add a dark/light toggle to Settings',
          status: 'waiting_approval',
          pr: 42,
          prTitle: 'Dark mode toggle in Settings',
          createdAt: '2026-10-11T10:00:00Z'),
      requestJson('dev_2',
          text: 'Show pending approvals on the Tasks page',
          status: 'ci_failed',
          pr: 41,
          createdAt: '2026-10-10T10:00:00Z'),
      requestJson('dev_1',
          text: 'Let me rename a conversation in the Assistant',
          status: 'failed',
          failureReason: 'The agent made no changes.',
          createdAt: '2026-10-09T10:00:00Z'),
    ];

void main() {
  testWidgets('shows progress while the role and the list load',
      (tester) async {
    final api = FakeDeveloperApi(requests: _mixed())..holdList = Completer();
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c, settle: false);
    expect(find.text('Loading developer requests…'), findsOneWidget);
    api.holdList!.complete();
    await tester.pumpAndSettle();
    expect(find.text('Dark mode toggle in Settings'), findsOneWidget);
    expect(find.text('Waiting for approval'), findsOneWidget);
    expect(find.text('Your approval'), findsNothing,
        reason: 'no merge approval was opened yet');
  });

  testWidgets('non-owners see what it is and who can use it', (tester) async {
    final api = FakeDeveloperApi(role: 'member');
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(find.text('Developer mode is for workspace owners'), findsOneWidget);
    expect(find.textContaining('Your role here is member'), findsOneWidget);
    expect(find.textContaining('Ask an owner'), findsOneWidget);
    expect(find.byKey(const Key('developer-draft')), findsNothing);
    expect(api.calls('GET', '/developer/'), isEmpty);
  });

  testWidgets('a 403 from the API shows the owner-only explanation',
      (tester) async {
    final api = FakeDeveloperApi()
      ..failures['GET setup'] =
          (403, 'Developer mode is not enabled for this organization.');
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(find.byKey(const Key('developer-owner-only')), findsOneWidget);
    expect(find.textContaining('not enabled for this organization'),
        findsOneWidget);
  });

  testWidgets('a role check that fails offers Try again', (tester) async {
    final api = FakeDeveloperApi()..failures['GET role'] = (503, 'Down');
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(find.text('Your role could not be checked'), findsOneWidget);
    api.failures.clear();
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('No developer requests yet'), findsOneWidget);
  });

  testWidgets('a list that fails to load says why; Try again recovers',
      (tester) async {
    final api = FakeDeveloperApi(requests: _mixed())
      ..failures['GET list'] = (500, 'Database down');
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(find.text('Developer requests could not be loaded'), findsOneWidget);
    expect(find.textContaining('Database down'), findsOneWidget);
    api.failures.clear();
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('Dark mode toggle in Settings'), findsOneWidget);
  });

  testWidgets('setup incomplete lists setting names, never values',
      (tester) async {
    final api = FakeDeveloperApi(
        setup: setupJson(configured: false, missing: [
      'ANUM_DEVELOPER_GITHUB_REPO',
      'ANUM_DEVELOPER_GITHUB_TOKEN',
      'ANUM_DEVELOPER_TENANT_ID',
    ]));
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(find.text('Set up developer mode'), findsOneWidget);
    expect(find.text('ANUM_DEVELOPER_GITHUB_TOKEN'), findsOneWidget);
    expect(find.text('ANUM_DEVELOPER_TENANT_ID'), findsOneWidget);
    expect(find.textContaining('ANTHROPIC_API_KEY and ANUM_DEV_PUSH_TOKEN'),
        findsOneWidget);
    expect(find.textContaining('never paste values'), findsOneWidget);
    // No form until it is set up; the empty state says what comes next.
    expect(find.byKey(const Key('developer-draft')), findsNothing);
    expect(
        find.textContaining('Once developer mode is set up'), findsOneWidget);
  });

  testWidgets('empty: an example fills the request; sending lists it',
      (tester) async {
    final api = FakeDeveloperApi();
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(find.text('No developer requests yet'), findsOneWidget);
    expect(
        find.text(
            '1 of 10 requests used today · builds example/anum from main'),
        findsOneWidget);
    final send = find.byKey(const Key('developer-send'));
    expect(tester.widget<FilledButton>(send).onPressed, isNull);

    await _scrollTo(tester, find.text('Add a dark/light toggle to Settings'));
    await tester.tap(find.text('Add a dark/light toggle to Settings'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('developer-draft')))
            .controller!
            .text,
        'Add a dark/light toggle to Settings');
    await tester.ensureVisible(send);
    await tester.tap(send);
    await tester.pumpAndSettle();
    expect(api.calls('POST', '/developer/requests').single.body!['source'],
        'text');
    expect(find.textContaining('Sent to the developer'), findsOneWidget);
    expect(find.text('Queued'), findsOneWidget);
    expect(find.textContaining('One request at a time'), findsOneWidget);
    expect(tester.widget<FilledButton>(send).onPressed, isNull,
        reason: 'one request at a time');
  });

  testWidgets('a protected request needs the tick; a used-up budget explains',
      (tester) async {
    final api = FakeDeveloperApi()
      ..failures['POST create'] = (402, 'Budget used up until 1 November.');
    var settings = 0;
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c, onOpenSettings: () => settings++);
    await tester.enterText(find.byKey(const Key('developer-draft')),
        'Rotate the API keys in the workflow files');
    await tester.pump();
    await tester.tap(find.byKey(const Key('developer-send')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Tick "Allow changes'), findsOneWidget);
    expect(api.calls('POST', '/developer/requests'), isEmpty);

    await tester.tap(find.byKey(const Key('developer-allow-protected')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('developer-send')));
    await tester.pumpAndSettle();
    expect(
        api
            .calls('POST', '/developer/requests')
            .single
            .body!['allow_protected_paths'],
        true);
    expect(find.text('Monthly model budget reached'), findsOneWidget);
    await _scrollTo(tester, find.text('Open Settings'));
    await tester.tap(find.text('Open Settings'));
    expect(settings, 1);
  });

  testWidgets('long lists page; search narrows and clears', (tester) async {
    final api = FakeDeveloperApi(requests: manyRequests(25));
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(c.items, hasLength(20));
    final more = find.byKey(const Key('developer-more'));
    await _scrollTo(tester, more);
    await tester.tap(more);
    await tester.pumpAndSettle();
    expect(c.items, hasLength(25));
    expect(more, findsNothing);

    await tester.dragUntilVisible(find.byKey(const Key('developer-search')),
        find.byType(Scrollable).first, const Offset(0, 300));
    await tester.enterText(
        find.byKey(const Key('developer-search')), 'number 25');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(c.items.single.id, 'dev_25');
    expect(find.text('Clear'), findsOneWidget);

    await tester.enterText(
        find.byKey(const Key('developer-search')), 'nothing like this');
    await tester.tap(find.text('Search'));
    await tester.pumpAndSettle();
    expect(find.text('No matching requests'), findsOneWidget);
    await tester.tap(find.text('Clear search'));
    await tester.pumpAndSettle();
    expect(c.items, hasLength(20));
    expect(c.query, isEmpty);
  });

  testWidgets(
      'detail: timeline, links, then Approve & merge shows the hash and '
      'merges only on Confirm merge', (tester) async {
    final links = _Links();
    final api = FakeDeveloperApi(requests: _mixed());
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c, openLink: links.open);
    await _open(tester, 'dev_3');

    expect(find.text('Developer request'), findsOneWidget);
    final semantics = tester.ensureSemantics();
    expect(find.bySemanticsLabel('Pull request opened: done'), findsOneWidget);
    expect(find.bySemanticsLabel('Waiting for approval: in progress'),
        findsOneWidget);
    expect(find.bySemanticsLabel('Merged: not started'), findsOneWidget);
    semantics.dispose();
    expect(find.byKey(const Key('developer-checks')), findsOneWidget);
    expect(find.text('3 of 3 checks passed'), findsOneWidget);

    await _scrollTo(tester, find.byKey(const Key('developer-pr-link')));
    await tester.tap(find.byKey(const Key('developer-pr-link')));
    await tester.tap(find.byKey(const Key('developer-run-link')));
    await tester.pumpAndSettle();
    expect(links.opened.map((u) => u.toString()), [
      'https://github.com/example/anum/pull/42',
      'https://github.com/example/anum/actions/runs/7',
    ]);

    final approve = find.byKey(const Key('developer-approve'));
    await _scrollTo(tester, approve);
    await tester.tap(approve);
    await tester.pumpAndSettle();
    expect(api.calls('POST', '/merge-approval'), hasLength(1));
    expect(api.calls('POST', '/approve'), isEmpty);
    expect(find.text('Approve the merge'), findsOneWidget);
    expect(find.text(exampleHash), findsOneWidget);
    expect(find.text('Sends to api.github.com'), findsOneWidget);
    expect(find.text('developer.merge_pull_request', findRichText: true),
        findsNothing);
    expect(find.text('Tool: developer.merge_pull_request'), findsOneWidget);

    final confirm = find.byKey(const Key('developer-confirm-merge'));
    await _scrollTo(tester, confirm);
    await tester.tap(confirm);
    await tester.pumpAndSettle();
    expect(api.calls('POST', '/approve').single.body,
        {'payload_hash': exampleHash});
    expect(find.text('Merged pull request #42.'), findsOneWidget);
    expect(find.text('Merged'), findsWidgets);
    expect(find.byKey(const Key('developer-confirm-merge')), findsNothing);
  });

  testWidgets('Not now hides the approval; it can be opened again',
      (tester) async {
    final api = FakeDeveloperApi(requests: [
      requestJson('dev_1',
          status: 'waiting_approval', pr: 42, approval: approvalJson('a_1'))
    ]);
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    expect(find.text('Your approval'), findsOneWidget);
    await _open(tester, 'dev_1');
    await _scrollTo(tester, find.text('Not now'));
    await tester.tap(find.text('Not now'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('developer-merge-review')), findsNothing);
    expect(find.byKey(const Key('developer-approve')), findsOneWidget);
  });

  testWidgets('Cancel asks first and only then cancels', (tester) async {
    final api = FakeDeveloperApi(requests: [requestJson('dev_1')]);
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c);
    await _open(tester, 'dev_1');
    final cancel = find.byKey(const Key('developer-cancel'));
    await _scrollTo(tester, cancel);
    await tester.tap(cancel);
    await tester.pumpAndSettle();
    expect(find.text('Cancel this request?'), findsOneWidget);
    await tester.tap(find.text('Keep it'));
    await tester.pumpAndSettle();
    expect(api.calls('POST', '/cancel'), isEmpty);

    await tester.tap(cancel);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Cancel request'));
    await tester.pumpAndSettle();
    expect(api.calls('POST', '/cancel'), hasLength(1));
    expect(find.text('Cancelled'), findsOneWidget);
    expect(find.byKey(const Key('developer-cancel')), findsNothing);
  });

  testWidgets(
      'failed requests show the reason; a link that will not open '
      'says so', (tester) async {
    final links = _Links()..works = false;
    final api = FakeDeveloperApi(requests: _mixed());
    final c = await buildDeveloper(api);
    await pumpDeveloper(tester, c, openLink: links.open);
    await _open(tester, 'dev_1');
    expect(find.text('The agent made no changes.'), findsWidgets);
    expect(find.byKey(const Key('developer-cancel')), findsNothing);
    final run = find.byKey(const Key('developer-run-link'));
    await _scrollTo(tester, run);
    await tester.tap(run);
    await tester.pumpAndSettle();
    expect(find.textContaining('The link could not be opened'), findsOneWidget);
  });

  group('layout', () {
    for (final (size, scale) in [
      (const Size(320, 568), 1.0),
      (const Size(320, 568), 2.0),
      (const Size(390, 844), 1.0),
      (const Size(390, 844), 2.0),
      (const Size(768, 1024), 1.0),
      (const Size(1024, 768), 1.0),
      (const Size(1440, 900), 1.0),
      (const Size(1440, 900), 2.0),
    ]) {
      testWidgets(
          'fits ${size.width.toInt()}x${size.height.toInt()} at '
          '${(scale * 100).toInt()}% text', (tester) async {
        final api = FakeDeveloperApi(requests: [
          ..._mixed(),
          ...manyRequests(4, prefix: 'dev_old_'),
        ]);
        final c = await buildDeveloper(api);
        await pumpDeveloper(tester, c, size: size, textScale: scale);
        expect(tester.takeException(), isNull);
        expect(find.byKey(const Key('developer-draft')), findsOneWidget);

        // The whole list scrolls without overflow.
        await _scrollTo(
            tester, find.byKey(const Key('developer-request-dev_old_1')));
        expect(tester.takeException(), isNull);
        await tester.dragUntilVisible(
            find.byKey(const Key('developer-request-dev_3')),
            find.byType(ListView).last,
            const Offset(0, 300));
        await tester.pumpAndSettle();

        // The detail with the merge approval open.
        await tester.tap(find.byKey(const Key('developer-request-dev_3')));
        await tester.pumpAndSettle();
        final approve = find.byKey(const Key('developer-approve'));
        await _scrollTo(tester, approve);
        await tester.tap(approve);
        await tester.pumpAndSettle();
        await _scrollTo(
            tester, find.byKey(const Key('developer-confirm-merge')));
        expect(tester.takeException(), isNull);
        final hash = tester.getRect(find.text(exampleHash));
        expect(hash.right, lessThanOrEqualTo(size.width));
      });
    }

    testWidgets('setup and owner-only states fit 320x568 at 200% text',
        (tester) async {
      final api = FakeDeveloperApi(
          setup: setupJson(configured: false, missing: [
        'ANUM_DEVELOPER_GITHUB_REPO',
        'ANUM_DEVELOPER_GITHUB_TOKEN',
        'ANUM_DEVELOPER_TENANT_ID',
        'ANUM_DEVELOPER_GITHUB_API_URL',
      ]));
      final c = await buildDeveloper(api);
      await pumpDeveloper(tester, c, size: const Size(320, 568), textScale: 2);
      await _scrollTo(tester, find.textContaining('Once developer mode'));
      expect(tester.takeException(), isNull);

      final member = await buildDeveloper(FakeDeveloperApi(role: 'viewer'));
      await pumpDeveloper(tester, member,
          size: const Size(320, 568), textScale: 2);
      expect(
          find.text('Developer mode is for workspace owners'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('Arabic right-to-left at 200% text keeps the list usable',
        (tester) async {
      final api = FakeDeveloperApi(requests: _mixed());
      final c = await buildDeveloper(api);
      await pumpDeveloper(tester, c,
          size: const Size(390, 844),
          textScale: 2,
          direction: TextDirection.rtl);
      expect(tester.takeException(), isNull);
      final tile = find.byKey(const Key('developer-request-dev_3'));
      await _scrollTo(tester, tile);
      final chevron = tester.getRect(find.descendant(
          of: tile, matching: find.byIcon(Icons.chevron_right)));
      final title = tester.getRect(find.text('Dark mode toggle in Settings'));
      // The chevron sits at the start (left) in right-to-left layouts.
      expect(chevron.center.dx, lessThan(title.center.dx));
      await tester.tap(tile);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  });
}
