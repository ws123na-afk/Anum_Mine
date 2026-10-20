import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/learning/learning_controller.dart';
import 'package:anum_mobile/features/learning/learning_models.dart';
import 'package:anum_mobile/features/learning/learning_repository.dart';
import 'package:anum_mobile/features/learning/learning_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, Object?> _overview(
        {String state = 'on', bool enabled = true, bool member = false}) =>
    {
      'state': state,
      'counts': {
        'signals': {'rating_up': 2, 'draft_edited': 1},
        'preferences': 1,
        'routines_proposed': 1,
        'suggestions_open': 2,
      },
      'profile': {
        'enabled': enabled,
        'derived': {
          'tone': 'formal',
          'length': 'short',
          'target_words': 24,
          'greeting': 'Dear',
          'sign_off': 'Best regards',
          'language': 'en',
          'contact_languages': {'supplier@example.com': 'ar'},
          'samples': 3,
        },
        'overrides': <String, Object?>{},
      },
      'can_manage_automations': !member,
      'can_apply_autonomy': !member,
    };

/// An in-memory API that keeps state, like the server does for one user.
class _FakeLearning implements LearningRepository {
  _FakeLearning({this.member = false, this.failOverview, this.empty = false});
  final bool member, empty;
  ApiException? failOverview;
  bool enabled = true;
  final calls = <String>[];
  StyleOverrides? saved;
  late List<LearnedPreference> prefs = empty
      ? []
      : [
          const LearnedPreference(
              id: 'pref_1',
              scope: 'surface:whatsapp',
              text: 'Reply to customers in Arabic.',
              fromSuggestion: false)
        ];
  late List<LearnedRoutine> routineList = empty
      ? []
      : [
          const LearnedRoutine(
              id: 'routine_1',
              title:
                  'Every Sunday around 09:00: Check Amazon orders, then Email the supplier',
              status: 'proposed',
              cron: '0 9 * * 0',
              timezone: 'Asia/Riyadh',
              steps: ['Check Amazon orders', 'Email the supplier'],
              occurrences: 4)
        ];
  late List<ImprovementSuggestion> suggestions = empty
      ? []
      : [
          ImprovementSuggestion(
              id: 's1',
              kind: 'instruction',
              title: 'You rejected external.action 2 times',
              body: 'Applying adds: Confirm the recipient first.',
              status: 'open',
              needsPermission: null,
              changesPolicy: false),
          ImprovementSuggestion(
              id: 's2',
              kind: 'autonomy',
              title: 'Let medium-risk actions run without asking',
              body: 'You approved all 6 medium-risk actions.',
              status: 'open',
              needsPermission: member ? 'policy:manage' : null,
              changesPolicy: true),
        ];
  late List<LearningSignal> signalList = empty
      ? []
      : List.generate(
          25,
          (i) => LearningSignal(
              id: 'signal_$i',
              kind: i.isEven ? 'rating_up' : 'draft_edited',
              surface: i.isEven ? 'chat' : 'email',
              createdAt: DateTime.utc(2026, 10, 11),
              contact: i.isEven ? null : 'supplier@example.com',
              finalText:
                  i.isEven ? null : 'Dear supplier, the order is late.'));

  @override
  Future<LearningOverview> overview() async {
    calls.add('overview');
    final failure = failOverview;
    if (failure != null) {
      failOverview = null;
      throw failure;
    }
    return LearningOverview.fromJson(_overview(
        state: enabled ? 'on' : 'off_by_user',
        enabled: enabled,
        member: member));
  }

  @override
  Future<LearningOverview> setEnabled(bool value) async {
    calls.add('enabled:$value');
    enabled = value;
    return overview();
  }

  @override
  Future<void> saveStyle(StyleOverrides overrides) async {
    calls.add('style');
    saved = overrides;
  }

  @override
  Future<int> resetStyle() async {
    calls.add('reset');
    return 3;
  }

  @override
  Future<LearningPage<LearnedPreference>> preferences(
      {String query = '', int offset = 0}) async {
    final found = prefs
        .where((p) => p.text.toLowerCase().contains(query.toLowerCase()))
        .toList();
    return LearningPage(
        found.skip(offset).take(learningPageSize).toList(), found.length);
  }

  @override
  Future<void> addPreference(String scope, String text) async {
    calls.add('add:$scope:$text');
    prefs = [
      LearnedPreference(
          id: 'pref_${prefs.length + 2}',
          scope: scope,
          text: text,
          fromSuggestion: false),
      ...prefs
    ];
  }

  @override
  Future<void> updatePreference(String id, String scope, String text) async {
    calls.add('update:$id');
  }

  @override
  Future<void> deletePreference(String id) async {
    calls.add('delete:$id');
    prefs = prefs.where((p) => p.id != id).toList();
  }

  @override
  Future<List<LearnedRoutine>> routines() async => routineList;

  @override
  Future<(List<LearnedRoutine>, int)> detectRoutines(String timezone) async {
    calls.add('detect:$timezone');
    return (routineList, 0);
  }

  @override
  Future<LearnedRoutine> routineAction(String id, String action) async {
    calls.add('routine:$action');
    if (member) throw const ApiException(403, 'Missing automation:manage');
    final r = routineList.single;
    final next = LearnedRoutine(
        id: r.id,
        title: r.title,
        status: action == 'accept' ? 'accepted' : 'confirmed',
        cron: r.cron,
        timezone: r.timezone,
        steps: r.steps,
        occurrences: r.occurrences);
    routineList = [next];
    return next;
  }

  @override
  Future<void> deleteRoutine(String id) async => calls.add('deleteRoutine');

  @override
  Future<List<ImprovementNote>> notes() async => empty
      ? []
      : [
          ImprovementNote.fromJson(JsonReader.of({
            'note': {
              'week': '2026-W41',
              'summary': {'ratings_up': 2, 'approvals_rejected': 2},
            },
            'suggestions': <Object?>[],
          })),
        ]
          .map((n) => ImprovementNote(
              week: n.week, summary: n.summary, suggestions: suggestions))
          .toList();

  @override
  Future<ImprovementSuggestion> suggestionAction(
      String id, String action) async {
    calls.add('suggestion:$id:$action');
    final s = suggestions.firstWhere((s) => s.id == id);
    final next = ImprovementSuggestion(
        id: s.id,
        kind: s.kind,
        title: s.title,
        body: s.body,
        status: action == 'apply' ? 'applied' : 'dismissed',
        needsPermission: s.needsPermission,
        changesPolicy: s.changesPolicy);
    suggestions = [for (final x in suggestions) x.id == id ? next : x];
    return next;
  }

  @override
  Future<LearningPage<LearningSignal>> signals(
      {String query = '', int offset = 0}) async {
    final found = signalList
        .where((s) =>
            query.isEmpty || (s.finalText ?? '').toLowerCase().contains(query))
        .toList();
    return LearningPage(
        found.skip(offset).take(learningPageSize).toList(), found.length);
  }

  @override
  Future<void> deleteSignal(String id) async {
    calls.add('deleteSignal:$id');
    signalList = signalList.where((s) => s.id != id).toList();
  }
}

Future<void> _pump(WidgetTester tester, LearningController controller,
    {Size size = const Size(390, 4000), double scale = 1}) async {
  await tester.binding.setSurfaceSize(size);
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(MaterialApp(
      key: ValueKey('$size $scale'),
      theme: AnumTheme.dark(),
      home: MediaQuery.withClampedTextScaling(
          minScaleFactor: scale,
          maxScaleFactor: scale,
          child: LearningScreen(
              controller: controller, onOpenChatMemory: () {}))));
  await tester.pumpAndSettle();
}

Future<void> _tapText(WidgetTester tester, String text) async {
  final target = find.text(text).first;
  await tester.scrollUntilVisible(target, 200,
      scrollable: find.byType(Scrollable).first);
  await tester.tap(target);
  await tester.pumpAndSettle();
}

void main() {
  test('the overview reads every state and counts', () {
    final overview = LearningOverview.fromJson(_overview());
    expect(overview.state, LearningState.on);
    expect(overview.signals, 3);
    expect(overview.derived.contactLanguages, {'supplier@example.com': 'ar'});
    expect(learningStateOf('off_chat_memory'), LearningState.offChatMemory);
    expect(learningStateOf('something_new'), LearningState.unknown);
    expect(describeState(LearningState.offOnServer).$2,
        contains('ANUM_LEARNING_ENABLED'));
    expect(scopeLabel('contact:+966500000000'), 'Contact: +966500000000');
    expect(scopeLabel('surface:email'), 'Email drafts');
  });

  testWidgets(
      'the main path: switch, style, a preference, a routine, a suggestion and feedback',
      (tester) async {
    final api = _FakeLearning();
    final controller = LearningController(api);
    await _pump(tester, controller);

    expect(find.text('What ANUM learned'), findsOneWidget);
    expect(find.textContaining('Learned from 3 drafts'), findsOneWidget);
    expect(find.text('Learned: Formal'), findsOneWidget);

    await tester.tap(find.byKey(const Key('learning-switch')));
    await tester.pumpAndSettle();
    expect(api.calls, contains('enabled:false'));
    expect(find.text('Learning is off. Nothing new will be learned.'),
        findsOneWidget);
    await tester.tap(find.byKey(const Key('learning-switch')));
    await tester.pumpAndSettle();

    await tester.enterText(
        find.widgetWithText(TextField, 'Notes for ANUM'), 'Use first names.');
    await _tapText(tester, 'Save my style');
    expect(api.saved?.notes, 'Use first names.');
    expect(
        find.text('Saved. Drafts use your style from now on.'), findsOneWidget);

    await _tapText(tester, 'Add preference');
    await tester.enterText(find.widgetWithText(TextField, 'Preference'),
        'Always include the order number.');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(api.calls, contains('add:all:Always include the order number.'));
    expect(find.text('Always include the order number.'), findsOneWidget);

    await tester.tap(find.byTooltip('Delete "Reply to customers in Arabic."'));
    await tester.pumpAndSettle();
    expect(api.calls, contains('delete:pref_1'));

    await _tapText(tester, 'Apply');
    expect(api.calls, contains('suggestion:s1:apply'));
    expect(find.text('Applied. It is now one of your learned preferences.'),
        findsOneWidget);

    await _tapText(tester, 'Create paused automation');
    expect(find.textContaining('Paused automation ·'), findsOneWidget);
    await _tapText(tester, 'Start the schedule');
    expect(find.textContaining('Scheduled ·'), findsOneWidget);

    expect(find.text('Show more (20 of 25)'), findsOneWidget);
    await _tapText(tester, 'Show more (20 of 25)');
    expect(find.text('Showing 25 of 25.'), findsOneWidget);
    final delete = find.byTooltip('Delete this feedback').first;
    await tester.scrollUntilVisible(delete, 200,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(delete);
    await tester.pumpAndSettle();
    expect(api.calls, contains('deleteSignal:signal_0'));
  });

  testWidgets('contact languages, no greeting and the routines time zone',
      (tester) async {
    final api = _FakeLearning();
    await _pump(tester, LearningController(api));

    Future<void> scrollTo(Finder f) async {
      await tester.ensureVisible(f);
      await tester.pumpAndSettle();
    }

    await scrollTo(find.byKey(const Key('contact-add')));
    await tester.tap(find.byKey(const Key('contact-add')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('contact-0')), ' buyer@example.com ');
    await tester.tap(find.byKey(const Key('contact-add')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('contact-1')), '');
    await tester.tap(find.byKey(const Key('contact-language-0')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('English').last);
    await tester.pumpAndSettle();

    await scrollTo(find.byKey(const Key('style-no-greeting')));
    await tester.tap(find.byKey(const Key('style-no-greeting')));
    await tester.pumpAndSettle();
    await _tapText(tester, 'Save my style');
    // A blank row is dropped, the typed one is trimmed and keeps its language.
    expect(api.saved?.contactLanguages, {'buyer@example.com': 'en'});
    expect(api.saved?.greeting, '');
    expect(api.saved?.signOff, isNull);

    await scrollTo(find.byKey(const Key('contact-remove-0')));
    await tester.tap(find.byKey(const Key('contact-remove-0')));
    await tester.pumpAndSettle();
    await _tapText(tester, 'Save my style');
    expect(api.saved?.contactLanguages, isNull);

    await scrollTo(find.byKey(const Key('routine-timezone')));
    await tester.enterText(
        find.byKey(const Key('routine-timezone')), 'Asia/Riyadh');
    await _tapText(tester, 'Find routines');
    expect(api.calls, contains('detect:Asia/Riyadh'));
  });

  testWidgets('empty lists say what to do next', (tester) async {
    await _pump(tester, LearningController(_FakeLearning(empty: true)));
    expect(find.textContaining('No preferences yet.'), findsOneWidget);
    expect(find.textContaining('No notes yet.'), findsOneWidget);
    expect(find.textContaining('No routines yet.'), findsOneWidget);
    expect(find.textContaining('No feedback yet.'), findsOneWidget);
  });

  testWidgets(
      'a member is told who can create automations and apply autonomy changes',
      (tester) async {
    await _pump(tester, LearningController(_FakeLearning(member: true)));
    expect(find.text(routinePermissionMessage), findsOneWidget);
    expect(find.text(autonomyPermissionMessage), findsOneWidget);
    final accept =
        find.widgetWithText(FilledButton, 'Create paused automation');
    expect(tester.widget<FilledButton>(accept).onPressed, isNull);
  });

  testWidgets('permission denied and failures explain and retry',
      (tester) async {
    final denied =
        _FakeLearning(failOverview: const ApiException(403, 'Forbidden'));
    await _pump(tester, LearningController(denied));
    expect(find.text(learningPermissionMessage), findsOneWidget);

    final failing = _FakeLearning(
        failOverview: const ApiException(500, 'Database unavailable'));
    await _pump(tester, LearningController(failing),
        size: const Size(391, 4000));
    expect(
        find.textContaining(
            'Could not load what ANUM learned: Database unavailable'),
        findsOneWidget);
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Learned from 3 drafts'), findsOneWidget);
  });

  for (final scale in const [1.0, 2.0]) {
    testWidgets('fits every width at ${scale}x text', (tester) async {
      for (final width in const [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
        await _pump(tester, LearningController(_FakeLearning()),
            size: Size(width, 900), scale: scale);
        expect(tester.takeException(), isNull, reason: '$width at $scale');
        await tester.drag(
            find.byType(Scrollable).first, const Offset(0, -2000));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: '$width at $scale');
      }
    });
  }
}
