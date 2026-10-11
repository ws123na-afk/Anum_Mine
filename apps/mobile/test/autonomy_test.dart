// Activity feed and autonomy levels (docs/autonomy.md): JSON, controllers and
// screens against a fake repository, plus layouts at the gate widths and 200% text.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/admin/admin_controller.dart'
    show AdminPhase;
import 'package:anum_mobile/features/autonomy/autonomy_controller.dart';
import 'package:anum_mobile/features/autonomy/autonomy_models.dart';
import 'package:anum_mobile/features/autonomy/autonomy_repository.dart';
import 'package:anum_mobile/features/autonomy/autonomy_screens.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _now = DateTime.utc(2026, 10, 11, 12);

Map<String, Object?> _action(String id,
        {Map<String, Object?> extra = const {}}) =>
    {
      'id': id,
      'task_id': 'task_1',
      'tool': 'email.archive',
      'category': 'email',
      'action_kind': 'archive',
      'level': 'act_and_notify',
      'pattern_key': 'email.archive',
      'template': null,
      'summary': 'Archived email $id',
      'status': 'done',
      'requested_by': 'owner_a',
      'created_at': '2026-10-11T11:58:00Z',
      'reversible': true,
      'undo_available': true,
      'undo_expires_at': '2026-10-11T12:05:00Z',
      'undo_token': 'undo_example_$id',
      'can_undo': true,
      'undone_by': null,
      'pattern_level': null,
      ...extra,
    };

Map<String, Object?> _overview({bool owner = true}) => {
      'settings': {
        'category_levels': {'email': 'act_and_notify'},
        'amount_threshold': 0,
        'recipients_threshold': 5,
        'price_change_percent_threshold': 0,
        'daily_caps': {'email': 20},
        'quiet_hours': null,
        'blocked_recipients': <String>[],
        'blocked_domains': ['competitor.example'],
        'approved_templates': ['order_status'],
        'proposal_after': 3,
        'updated_by': 'owner_a',
        'updated_at': '2026-10-11T10:00:00Z',
      },
      'guardrails': {'max_auto_recipients': 8, 'spending_limit': 1000},
      'categories': [
        for (final c in autonomyCategories)
          {
            'category': c,
            'level': c == 'email' ? 'act_and_notify' : 'act_silently',
            'ceiling': c == 'commerce' ? 'ask_always' : null,
            'effective': 'act_silently',
          },
      ],
      'pending_proposals': 1,
      'can_manage_workspace': owner,
      'can_tighten': true,
    };

class _Fake implements AutonomyRepository {
  _Fake({this.owner = true, this.failUndo = false, this.saveForbidden = false});
  final bool owner, failUndo, saveForbidden;
  final calls = <String>[];
  Map<String, Object?>? saved;
  late List<Map<String, Object?>> rows = [
    for (var i = 0; i < 30; i++) _action('a$i'),
  ];

  @override
  Future<ActionPage> actions(
      {String? category, String? query, String? cursor, int limit = 25}) async {
    calls.add('actions:${category ?? ''}:${query ?? ''}:${cursor ?? ''}');
    var list = rows
        .where((r) =>
            (category == null ||
                category.isEmpty ||
                r['category'] == category) &&
            (query == null ||
                query.isEmpty ||
                (r['summary'] as String).contains(query)))
        .toList();
    if (cursor != null) {
      list = list.sublist(list.indexWhere((r) => r['id'] == cursor) + 1);
    }
    final page = list.take(limit).toList();
    return ActionPage(
        [for (final r in page) AutonomousAction.fromJson(JsonReader(r))],
        list.length > limit ? page.last['id'] as String : null);
  }

  @override
  Future<AutonomousAction> undo(AutonomousAction action) async {
    calls.add('undo:${action.id}:${action.undoToken}');
    if (failUndo) {
      throw const ApiException(
          502, 'The undo did not go through: the mailbox refused.');
    }
    return AutonomousAction.fromJson(JsonReader(_action(action.id, extra: {
      'status': 'undone',
      'undone_by': 'owner_a',
      'undo_token': null
    })));
  }

  @override
  Future<void> tighten(String actionId) async => calls.add('tighten:$actionId');

  @override
  Future<List<AutonomyProposal>> proposals() async => [
        AutonomyProposal.fromJson(const JsonReader({
          'id': 'aprop_1',
          'tool': 'whatsapp.send_template',
          'template': 'shipping',
          'category': 'whatsapp',
          'proposed_level': 'act_and_notify',
          'approvals': 3,
        })),
      ];

  @override
  Future<void> decide(String proposalId, {required bool accept}) async =>
      calls.add('decide:$proposalId:$accept');

  @override
  Future<AutonomyOverview> overview() async =>
      AutonomyOverview.fromJson(JsonReader(_overview(owner: owner)));

  @override
  Future<AutonomyOverview> save(Map<String, Object?> body) async {
    if (saveForbidden) {
      throw const ApiException(403,
          'Only a workspace owner can change autonomy for this workspace.');
    }
    saved = body;
    return AutonomyOverview.fromJson(
        JsonReader({..._overview(), 'settings': body}));
  }
}

Future<void> _pump(WidgetTester tester, Widget child,
    {Size size = const Size(390, 844), double scale = 1}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, content) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(scale)),
        child: content!),
    home: child,
  ));
  await tester.pumpAndSettle();
}

void main() {
  group('models', () {
    test('an action is undoable only while open, allowed and with a token', () {
      final a = AutonomousAction.fromJson(JsonReader(_action('a1')));
      expect(a.undoableAt(_now), isTrue);
      expect(a.undoableAt(DateTime.utc(2026, 10, 11, 12, 6)), isFalse);
      final other = AutonomousAction.fromJson(JsonReader(
          _action('a2', extra: {'can_undo': false, 'undo_token': null})));
      expect(other.undoableAt(_now), isFalse);
      expect(a.tightened().asksNow, isTrue);
    });

    test('the settings body keeps fields this client does not edit', () {
      final o = AutonomyOverview.fromJson(JsonReader(_overview()));
      expect(o.ceilings, {'commerce': 'ask_always'});
      expect(levelAllowed('act_and_notify', 'ask_always'), isFalse);
      final body = o.body(
          levels: {'email': 'ask_always'},
          recipientsThreshold: 3,
          amountThreshold: 10,
          approvedTemplates: ['order_status', 'shipping']);
      expect(body['blocked_domains'], ['competitor.example']);
      expect(body['daily_caps'], {'email': 20});
      expect(body['recipients_threshold'], 3);
      expect(body.containsKey('updated_by'), isFalse);
    });
  });

  group('activity controller', () {
    test('pages, filters, undoes, tightens and decides suggestions', () async {
      final repo = _Fake();
      final c = ActivityController(repo, clock: () => _now);
      await c.load();
      expect(c.phase, AdminPhase.ready);
      expect(c.items, hasLength(25));
      expect(c.proposals.single.sentence, contains('You approved it 3 times'));
      expect(c.canDecide, isTrue);
      await c.loadMore();
      expect(c.items, hasLength(30));
      expect(c.nextCursor, isNull);

      await c.undo(c.items.first);
      expect(repo.calls, contains('undo:a0:undo_example_a0'));
      expect(c.items.first.undone, isTrue);
      expect(c.notice, 'Undone: Archived email a0');

      await c.tighten(c.items[1]);
      expect(c.items.every((i) => i.asksNow), isTrue);

      await c.decide(c.proposals.single, accept: true);
      expect(c.proposals, isEmpty);
      expect(repo.calls, contains('decide:aprop_1:true'));

      c.search('a7');
      await Future<void>.delayed(Duration.zero);
      expect(c.items.single.id, 'a7');
      c.setCategory('commerce');
      await Future<void>.delayed(Duration.zero);
      expect(c.items, isEmpty);
      expect(c.filtered, isTrue);
    });

    test('a failed undo stays on its row; the action is still in effect',
        () async {
      final c = ActivityController(_Fake(failUndo: true), clock: () => _now);
      await c.load();
      await c.undo(c.items.first);
      expect(c.rowErrors['a0'], contains('did not go through'));
      expect(c.items.first.undone, isFalse);
    });
  });

  group('settings controller', () {
    test('owners save; floors are checked before sending', () async {
      final repo = _Fake();
      final c = AutonomySettingsController(repo);
      await c.load();
      expect(c.readOnly, isFalse);
      c.recipients = '12';
      expect(await c.save(), isFalse);
      expect(c.notice, contains('organization limit of 8'));
      c.recipients = '3';
      c.templates = 'order_status\n order_status\nshipping';
      c.setLevel('email', 'ask_always');
      expect(await c.save(), isTrue);
      expect(repo.saved!['approved_templates'], ['order_status', 'shipping']);
      expect((repo.saved!['category_levels'] as Map)['email'], 'ask_always');
    });

    test('a 403 on save turns the screen read-only with the API answer',
        () async {
      final c = AutonomySettingsController(_Fake(saveForbidden: true));
      await c.load();
      expect(await c.save(), isFalse);
      expect(c.readOnly, isTrue);
      expect(c.denied, contains('workspace owner'));
    });
  });

  group('screens', () {
    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      testWidgets('activity and autonomy lay out at ${width.toInt()} wide',
          (tester) async {
        final repo = _Fake();
        await _pump(
            tester,
            ActivityScreen(
                controller: ActivityController(repo, clock: () => _now)),
            size: Size(width, 900));
        expect(find.text('What ANUM did on its own'), findsOneWidget);
        expect(find.byKey(const Key('undo-a0')), findsOneWidget);
        expect(tester.takeException(), isNull);
        await _pump(
            tester,
            AutonomySettingsScreen(
                controller: AutonomySettingsController(repo)),
            size: Size(width, 900));
        expect(find.byKey(const Key('autonomy-save')), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('200% text: undo from the screen, read-only for members',
        (tester) async {
      final repo = _Fake(owner: false);
      final activity = ActivityController(repo, clock: () => _now);
      await _pump(tester, ActivityScreen(controller: activity), scale: 2);
      await tester.ensureVisible(find.byKey(const Key('undo-a0')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('undo-a0')));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('undo:a0:undo_example_a0'));
      expect(find.text('An owner of this workspace decides.'), findsOneWidget);
      expect(tester.takeException(), isNull);

      await _pump(tester,
          AutonomySettingsScreen(controller: AutonomySettingsController(repo)),
          scale: 2);
      expect(find.byKey(const Key('autonomy-owner-required')), findsOneWidget);
      expect(find.byKey(const Key('autonomy-save')), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('empty feed offers the settings as the next step',
        (tester) async {
      final repo = _Fake()..rows = [];
      var opened = false;
      await _pump(
          tester,
          ActivityScreen(
              controller: ActivityController(repo, clock: () => _now),
              onOpenSettings: () => opened = true));
      expect(find.text('Nothing done on its own yet'), findsOneWidget);
      await tester.ensureVisible(find.text('Autonomy settings'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Autonomy settings'));
      expect(opened, isTrue);
    });
  });
}
