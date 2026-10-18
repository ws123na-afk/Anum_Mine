// Phone calls on the phone (docs/calls.md#mobile): the dialpad rules, the
// tel: launch (mocked), the log-the-call sheet on return, contacts from the
// phone's picker, the AI summary and follow-up task, and every state, from a
// 320 px phone at 200 percent text to 1440 px, in English and Arabic.
// Every number is a plainly fake fixture.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/calls/calls_controller.dart';
import 'package:anum_mobile/features/calls/calls_models.dart';
import 'package:anum_mobile/features/calls/calls_repository.dart';
import 'package:anum_mobile/features/calls/calls_screen.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/calls_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/widgets/depth.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _now = DateTime.utc(2026, 10, 18, 9);

Map<String, Object?> _contact(String id, String name, String phone,
        {bool consent = false}) =>
    {
      'id': id,
      'name': name,
      'phone': phone,
      'phone_normalized': phone,
      'company': id == 'c1' ? 'Al Noor Trading' : null,
      'favourite': id == 'c1',
      'source': 'manual',
      'language': id == 'c2' ? 'ar' : 'en',
      'ai_consent': consent
          ? {
              'granted_at': _now.toIso8601String(),
              'recorded_by': 'owner',
              'method': 'written',
              'note': null
            }
          : null,
      'opted_out': false,
      'can_edit': true,
    };

Map<String, Object?> _call(String id,
        {String? notes, String? summary, Map<String, Object?>? follow}) =>
    {
      'id': id,
      'direction': 'outbound',
      'kind': 'phone',
      'status': 'logged',
      'outcome': 'answered',
      'started_at': _now.toIso8601String(),
      'duration_seconds': 95,
      'contact_id': 'c1',
      'contact_name': 'Sara Example',
      'phone': '+971501234567',
      'notes': notes,
      'summary': summary,
      'follow_up': follow,
      'summary_provenance': summary == null ? null : {'model': 'test-model'},
      'can_edit': true,
    };

Map<String, Object?> _aiItem(String id,
        {String status = 'pending_approval',
        String purpose = 'Confirm the delivery time on Thursday.'}) =>
    {
      'id': id,
      'requested_by': 'member',
      'contact_id': 'c2',
      'contact_name': 'عمر المثال',
      'phone_masked': '+971•••33',
      'purpose': purpose,
      'script': 'Hello, this is a reminder about your delivery.',
      'language': 'ar',
      'max_seconds': 120,
      'max_cost_cents': 10,
      'status': status,
      'approval_id': status == 'pending_approval' ? 'approval_1' : null,
      'approval': status == 'pending_approval'
          ? {'id': 'approval_1', 'status': 'pending', 'payload_hash': 'example'}
          : null,
      'created_at': _now.toIso8601String(),
      'error': status == 'failed' ? 'The contact withdrew consent.' : null,
    };

class FakeCallsRepository implements CallsRepository {
  bool denied = false, empty = false, failCalls = false;
  final logged = <Map<String, Object?>>[];
  final created = <Map<String, Object?>>[];
  final tasks = <String>[];
  final summaries = <String>[];
  Map<String, Object?> current =
      _call('call1', notes: 'Wants a quote for 40 tyres.');

  @override
  Future<CallsSetup> setup() async {
    if (denied) {
      throw const ApiException(403,
          'Phone calls are not part of the viewer role. Ask a workspace owner to make you a member.');
    }
    if (aiReady) {
      return CallsSetup.fromJson(JsonReader({
        'can_log': canLog,
        'can_manage': owner,
        'can_create_tasks': true,
        'model_ready': true,
        'provider_connected': true,
        'capabilities': {
          'browser_calls': true,
          'ai_calls': true,
          'cost_per_minute_cents': 5,
        },
      }));
    }
    return CallsSetup.fromJson(JsonReader({
      'can_log': canLog,
      'can_manage': owner,
      'can_create_tasks': true,
      'model_ready': true,
      'provider_connected': false,
      'capabilities': {
        'problem': 'No voice provider is connected.',
        'ai_calls': false,
        'ai_problem':
            'AI calls need a connected voice provider. A workspace owner connects one under Calls > Provider.',
      },
    }));
  }

  // -- AI calls and the provider ---------------------------------------------
  bool aiReady = false, owner = false, canLog = true;
  bool failAi = false, failProvider = false;
  final aiRequests = <Map<String, Object?>>[];
  final cancelled = <String>[];
  List<Map<String, Object?>> aiItems = [];

  @override
  Future<ProviderStatus> provider() async {
    if (failProvider) {
      throw const ApiException(503, 'The database did not answer.');
    }
    return ProviderStatus.fromJson(JsonReader(aiReady
        ? {
            'connected': true,
            'from_number': '+971500000000',
            'ai_calls_enabled': true,
            'recording_enabled': false,
            'cost_per_minute_cents': 5,
            'last_check_status': 'ok',
            'last_checked_at': _now.toIso8601String(),
            'webhooks_ready': true,
          }
        : {'connected': false, 'webhooks_ready': false}));
  }

  @override
  Future<CallsPage<AiCallRequest>> aiCalls({int offset = 0}) async {
    if (failAi) throw const ApiException(503, 'AI calls are unavailable.');
    return CallsPage(
        [for (final item in aiItems) AiCallRequest.fromJson(JsonReader(item))],
        aiItems.length);
  }

  @override
  Future<AiCallRequest> aiCall(String id) async => AiCallRequest.fromJson(
      JsonReader(aiItems.firstWhere((item) => item['id'] == id)));

  @override
  Future<AiCallRequest> requestAiCall({
    required String contactId,
    required String purpose,
    required String script,
    required String language,
    required int maxSeconds,
  }) async {
    final body = {
      'contact_id': contactId,
      'purpose': purpose,
      'script': script,
      'language': language,
      'max_seconds': maxSeconds,
    };
    aiRequests.add(body);
    final item = _aiItem('aicall_new', purpose: purpose);
    aiItems = [item, ...aiItems];
    return AiCallRequest.fromJson(JsonReader(item));
  }

  @override
  Future<AiCallRequest> cancelAiCall(String id) async {
    cancelled.add(id);
    aiItems = [
      for (final item in aiItems)
        item['id'] == id ? {...item, 'status': 'cancelled'} : item
    ];
    return aiCall(id);
  }

  @override
  Future<CallsPage<CallContact>> contacts(
      {String query = '', bool favourites = false, int offset = 0}) async {
    if (empty) return const CallsPage([], 0);
    final all = [
      _contact('c1', 'Sara Example', '+971501234567'),
      _contact('c2', 'عمر المثال', '+971502223333', consent: true),
      for (var i = 0; i < 30; i++)
        _contact('d$i', 'Driver ${i.toString().padLeft(2, '0')}',
            '+97150000${i.toString().padLeft(4, '0')}'),
    ]
        .where((c) =>
            query.isEmpty ||
            '${c['name']} ${c['phone']}'
                .toLowerCase()
                .contains(query.toLowerCase()))
        .toList();
    return CallsPage([
      for (final c in all.skip(offset).take(callsPageSize))
        CallContact.fromJson(JsonReader(c))
    ], all.length);
  }

  @override
  Future<CallContact> createContact(
      {required String name,
      required String phone,
      String? company,
      String source = 'manual'}) async {
    created.add({'name': name, 'phone': phone, 'source': source});
    return CallContact.fromJson(JsonReader(_contact('new', name, phone)));
  }

  @override
  Future<CallContact> updateContact(
          String id, Map<String, Object?> changes) async =>
      CallContact.fromJson(JsonReader(
          {..._contact(id, 'Sara Example', '+971501234567'), ...changes}));

  @override
  Future<void> deleteContact(String id) async {}

  @override
  Future<CallsPage<CallEntry>> calls(
      {String query = '',
      String outcome = '',
      String? contactId,
      int offset = 0}) async {
    if (failCalls) {
      throw const ApiException(503, 'The database did not answer.');
    }
    if (empty) return const CallsPage([], 0);
    return CallsPage([CallEntry.fromJson(JsonReader(current))], 1);
  }

  @override
  Future<CallEntry> call(String id) async =>
      CallEntry.fromJson(JsonReader(current));

  @override
  Future<CallEntry> logCall({
    String? contactId,
    String? phone,
    required String direction,
    required String outcome,
    int? durationSeconds,
    DateTime? startedAt,
    String? notes,
  }) async {
    logged.add({
      'contact_id': contactId,
      'phone': phone,
      'direction': direction,
      'outcome': outcome,
      'duration_seconds': durationSeconds,
      'notes': notes,
    });
    return CallEntry.fromJson(JsonReader(_call('call2', notes: notes)));
  }

  @override
  Future<CallEntry> updateCall(String id, Map<String, Object?> changes) async {
    current = {...current, ...changes};
    return CallEntry.fromJson(JsonReader(current));
  }

  @override
  Future<void> deleteCall(String id) async {}

  @override
  Future<CallEntry> summarize(String id) async {
    summaries.add(id);
    current = {
      ...current,
      'summary': 'Sara wants a quote for 40 tyres.',
      'summary_provenance': {'model': 'test-model'},
      'follow_up': {
        'text': 'Send Sara the quote',
        'due_date': null,
        'task_id': null
      },
    };
    return CallEntry.fromJson(JsonReader(current));
  }

  @override
  Future<CallEntry> saveFollowUp(
      String id, String text, String? dueDate) async {
    current = {
      ...current,
      'follow_up': {'text': text, 'due_date': dueDate, 'task_id': null}
    };
    return CallEntry.fromJson(JsonReader(current));
  }

  @override
  Future<CallEntry> followUpTask(String id) async {
    tasks.add(id);
    current = {
      ...current,
      'follow_up': {
        'text': 'Send Sara the quote',
        'due_date': null,
        'task_id': 'task_1'
      }
    };
    return CallEntry.fromJson(JsonReader(current));
  }
}

Future<void> _pump(
  WidgetTester tester,
  CallsController controller, {
  Size size = const Size(390, 844),
  double textScale = 1,
  Locale locale = const Locale('en'),
  PhoneDialer? dialer,
  ContactPicker? picker,
  DateTime Function()? now,
  VoidCallback? openApprovals,
}) async {
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
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: CallsScreen(
      controller: controller,
      dialer: dialer ?? (uri) async => true,
      pickContact: picker ?? () async => null,
      now: now ?? DateTime.now,
      openApprovals: openApprovals,
    ),
  ));
  await tester.pumpAndSettle();
}

String _en(String key) => callsEn[key]!;

void main() {
  group('dialpad rules', () {
    test('keys, + only first, long press 0, paste and delete', () {
      var number = '';
      for (final key in ['+', '9', '7', '1', '+', '*', '#', 'x']) {
        number = pressKey(number, key);
      }
      expect(number, '+971*#');
      expect(longPressZero(''), '+');
      expect(longPressZero('05'), '050');
      expect(backspace('+971'), '+97');
      expect(pasteNumber('', '+971 (50) 123-4567'), '+971501234567');
      expect(pasteNumber('', '٠٥٠١٢٣٤٥٦٧'), '0501234567');
      expect(pressKey('1' * 20, '2'), '1' * 20);
    });
    test('tel: URIs only for dialable numbers', () {
      expect(telUri('+971 50 123 4567').toString(), 'tel:+971501234567');
      expect(telUri('12'), isNull);
      expect(telUri('call me'), isNull);
      expect(isDialable('999'), isTrue);
    });
    test('durations', () {
      expect(durationText(65), '1:05');
      expect(durationText(3723), '1:02:03');
      expect(parseDuration('2:30'), 150);
      expect(parseDuration('٢:٣٠'), 150);
      expect(parseDuration('3m'), 180);
      expect(parseDuration('2:75'), isNull);
      expect(elapsedSeconds(_now, _now.add(const Duration(seconds: 65))), 65);
    });
    test('every English string has an Arabic one with the same placeholders',
        () {
      final holder = RegExp(r'\{(\w+)\}');
      expect(callsAr.keys.toSet(), callsEn.keys.toSet());
      for (final entry in callsEn.entries) {
        expect(holder.allMatches(callsAr[entry.key]!).map((m) => m[1]).toSet(),
            holder.allMatches(entry.value).map((m) => m[1]).toSet(),
            reason: entry.key);
      }
    });
  });

  testWidgets(
      'dialling opens the phone dialer and asks to log the call on return',
      (tester) async {
    final repo = FakeCallsRepository();
    final launched = <Uri>[];
    var clock = _now;
    await _pump(tester, CallsController(repo),
        dialer: (uri) async {
          launched.add(uri);
          return true;
        },
        now: () => clock);
    await tester.tap(find.text(_en('calls.tab.dial')));
    await tester.pumpAndSettle();
    await tester.longPress(find.byKey(const Key('dial-0')));
    for (final key in [
      '9',
      '7',
      '1',
      '5',
      '0',
      '1',
      '2',
      '3',
      '4',
      '5',
      '6',
      '7'
    ]) {
      await tester.tap(find.byKey(Key('dial-$key')));
    }
    await tester.pump();
    expect(find.text('+971501234567'), findsWidgets);
    expect(find.text('Sara Example'), findsOneWidget);
    await tester.tap(find.byKey(const Key('dial-call')));
    await tester.pumpAndSettle();
    expect(launched.single.toString(), 'tel:+971501234567');

    // The person talks on the phone, then comes back to ANUM.
    clock = _now.add(const Duration(minutes: 2, seconds: 5));
    for (final state in [
      AppLifecycleState.inactive,
      AppLifecycleState.hidden,
      AppLifecycleState.paused,
      AppLifecycleState.hidden,
      AppLifecycleState.inactive,
      AppLifecycleState.resumed,
    ]) {
      tester.binding.handleAppLifecycleStateChanged(state);
    }
    await tester.pumpAndSettle();
    expect(find.text('Call with Sara Example'), findsOneWidget);
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('log-duration')))
            .controller!
            .text,
        '2:05');
    await tester.tap(find.text(_en('calls.outcome.busy')));
    await tester.enterText(find.byKey(const Key('log-duration')), 'later');
    await tester.tap(find.byKey(const Key('log-save')));
    await tester.pumpAndSettle();
    expect(find.text(_en('calls.log.badDuration')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('log-duration')), '0:40');
    await tester.enterText(
        find.byKey(const Key('log-notes')), 'Call back at 4');
    await tester.tap(find.byKey(const Key('log-save')));
    await tester.pumpAndSettle();
    expect(repo.logged.single, {
      'contact_id': 'c1',
      'phone': null,
      'direction': 'outbound',
      'outcome': 'busy',
      'duration_seconds': 40,
      'notes': 'Call back at 4',
    });
    expect(find.text(_en('calls.log.saved')), findsOneWidget);
  });

  testWidgets('a dialer that does not open says so and logs nothing',
      (tester) async {
    final repo = FakeCallsRepository();
    final controller = CallsController(repo);
    await _pump(tester, controller, dialer: (uri) async => false);
    await tester.tap(find.byTooltip('Call Sara Example'));
    await tester.pumpAndSettle();
    expect(find.text(_en('calls.dial.failed')), findsOneWidget);
    expect(controller.pending, isNull);
  });

  testWidgets('contacts: search, paging, and adding one picked from the phone',
      (tester) async {
    final repo = FakeCallsRepository();
    await _pump(tester, CallsController(repo),
        picker: () async => const DeviceContact(
            name: 'Nadia Example', phone: '+971 55 444 1212'));
    expect(find.text('Sara Example'), findsOneWidget);
    final list = find
        .descendant(
            of: find.byType(ListView), matching: find.byType(Scrollable))
        .first;
    await tester.scrollUntilVisible(find.text(_en('calls.more')), 300,
        scrollable: list);
    await tester.tap(find.text(_en('calls.more')));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('Driver 29'), 300,
        scrollable: list);
    expect(find.text('Driver 29'), findsOneWidget);
    await tester.drag(find.byType(ListView).first, const Offset(0, 4000));
    await tester.pumpAndSettle();
    await tester.tap(find.text(_en('calls.pickFromPhone')));
    await tester.pumpAndSettle();
    expect(find.text(_en('calls.contact.fromPhone')), findsOneWidget);
    await tester.tap(find.byKey(const Key('contact-save')));
    await tester.pumpAndSettle();
    expect(repo.created.single, {
      'name': 'Nadia Example',
      'phone': '+971 55 444 1212',
      'source': 'device'
    });
  });

  testWidgets('a contact with a bad number is refused before saving',
      (tester) async {
    final repo = FakeCallsRepository();
    await _pump(tester, CallsController(repo));
    await tester.tap(find.text(_en('calls.add')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('contact-name')), 'Omar');
    await tester.enterText(find.byKey(const Key('contact-phone')), 'call me');
    await tester.tap(find.byKey(const Key('contact-save')));
    await tester.pumpAndSettle();
    expect(find.text(_en('calls.contact.badPhone')), findsOneWidget);
    expect(repo.created, isEmpty);
  });

  testWidgets('a picker failure is explained', (tester) async {
    await _pump(tester, CallsController(FakeCallsRepository()),
        picker: () async => throw PlatformException(
            code: 'unavailable', message: 'This phone has no contacts app.'));
    await tester.tap(find.text(_en('calls.pickFromPhone')));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('This phone has no contacts app.'), findsOneWidget);
  });

  testWidgets('a call is summarised and its follow-up saved as a task',
      (tester) async {
    final repo = FakeCallsRepository();
    await _pump(tester, CallsController(repo));
    await tester.tap(find.text(_en('calls.tab.recents')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sara Example').last);
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
        find.byKey(const Key('detail-summarise')), 200,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.byKey(const Key('detail-summarise')));
    await tester.pumpAndSettle();
    expect(find.text('Sara wants a quote for 40 tyres.'), findsWidgets);
    expect(find.textContaining('test-model'), findsOneWidget);
    await tester.scrollUntilVisible(find.byKey(const Key('detail-task')), 200,
        scrollable: find.byType(Scrollable).last);
    await tester.tap(find.byKey(const Key('detail-task')));
    await tester.pumpAndSettle();
    expect(repo.summaries, ['call1']);
    expect(repo.tasks, ['call1']);
    expect(find.text(_en('calls.detail.isTask')), findsOneWidget);
  });

  testWidgets('states: denied, empty and a failed list with retry',
      (tester) async {
    await _pump(tester, CallsController(FakeCallsRepository()..denied = true));
    expect(find.textContaining('Ask a workspace owner'), findsOneWidget);

    await _pump(tester, CallsController(FakeCallsRepository()..empty = true));
    expect(find.text(_en('calls.contacts.empty')), findsOneWidget);

    final failing = FakeCallsRepository()..failCalls = true;
    await _pump(tester, CallsController(failing));
    await tester.tap(find.text(_en('calls.tab.recents')));
    await tester.pumpAndSettle();
    expect(find.textContaining('The database did not answer.'), findsOneWidget);
    failing.failCalls = false;
    await tester.tap(find.text(_en('calls.retry')));
    await tester.pumpAndSettle();
    expect(find.text('Sara Example'), findsWidgets);
  });

  for (final (size, scale, locale) in [
    (const Size(320, 640), 2.0, const Locale('en')),
    (const Size(390, 844), 1.0, const Locale('ar')),
    (const Size(768, 1024), 1.5, const Locale('en')),
    (const Size(1440, 900), 1.0, const Locale('ar')),
  ]) {
    testWidgets(
        'every tab lays out at ${size.width.toInt()} px, ${scale}x text, ${locale.languageCode}',
        (tester) async {
      final repo = FakeCallsRepository()
        ..aiReady = true
        ..owner = true
        ..aiItems = [
          _aiItem('aicall_1'),
          _aiItem('aicall_2', status: 'failed')
        ];
      await _pump(tester, CallsController(repo),
          size: size, textScale: scale, locale: locale);
      final l = AnumLocalizations(locale);
      for (final tab in [
        'calls.tab.contacts',
        'calls.tab.dial',
        'calls.tab.recents',
        'calls.tab.ai',
      ]) {
        // At large text the tab strip scrolls: bring the tab into view first.
        await tester.ensureVisible(find.text(l.t(tab)));
        await tester.pumpAndSettle();
        await tester.tap(find.text(l.t(tab)));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: tab);
      }
      // The last tab is really open: the provider card and the AI call form.
      expect(find.byKey(const Key('provider-card')), findsOneWidget);
      final list = _aiList();
      await tester.scrollUntilVisible(find.byKey(const Key('ai-form')), 300,
          scrollable: list);
      await tester.scrollUntilVisible(find.byKey(const Key('ai-aicall_2')), 300,
          scrollable: list);
      expect(tester.takeException(), isNull);
      if (locale.languageCode == 'ar') {
        expect(find.text('المكالمات الهاتفية'), findsOneWidget);
        expect(
            Directionality.of(
                tester.element(find.byKey(const Key('ai-aicall_2')))),
            TextDirection.rtl);
      }
    });
  }

  group('AI calls', () {
    test('the blocker follows the web order and the cost is per started minute',
        () {
      CallContact contact({bool consent = true, bool optedOut = false}) =>
          CallContact.fromJson(JsonReader({
            ..._contact('c9', 'Test', '+971500000009', consent: consent),
            'opted_out': optedOut,
          }));
      CallsSetup setup({bool canLog = true, bool ai = true}) =>
          CallsSetup.fromJson(JsonReader({
            'can_log': canLog,
            'capabilities': {'ai_calls': ai},
          }));
      expect(aiCallBlocker(contact(), null), 'calls.ai.blocked.role');
      expect(aiCallBlocker(contact(), setup(canLog: false)),
          'calls.ai.blocked.role');
      expect(aiCallBlocker(contact(optedOut: true), setup()),
          'calls.ai.blocked.optedOut');
      expect(aiCallBlocker(contact(consent: false), setup()),
          'calls.ai.blocked.noConsent');
      expect(
          aiCallBlocker(contact(), setup(ai: false)), 'calls.ai.blocked.off');
      expect(aiCallBlocker(contact(), setup()), isNull);
      expect(
          consentingContacts(
                  [contact(), contact(consent: false), contact(optedOut: true)])
              .length,
          1);
      expect(aiCostText(120, 5), r'$0.10');
      expect(aiCostText(121, 5), r'$0.15');
      expect(aiCostText(60, null), r'$0.00');
      expect(clampAiSeconds(5), 30);
      expect(clampAiSeconds(5000), 900);
      expect(clampAiSeconds(null), 30);
    });

    Future<void> openAi(WidgetTester tester) async {
      // The tab strip scrolls on a phone: bring the tab into view first.
      await tester.ensureVisible(find.text(_en('calls.tab.ai')));
      await tester.pumpAndSettle();
      await tester.tap(find.text(_en('calls.tab.ai')));
      await tester.pumpAndSettle();
    }

    testWidgets(
        'a member asks for an AI call for a consenting contact only, and it waits for approval',
        (tester) async {
      final repo = FakeCallsRepository()..aiReady = true;
      var approvals = 0;
      await _pump(tester, CallsController(repo),
          size: const Size(768, 1024), openApprovals: () => approvals++);
      await openAi(tester);
      // Only the consenting contact is offered.
      expect(find.text('عمر المثال'), findsOneWidget);
      expect(find.text('Sara Example'), findsNothing);
      // Too short: explained, nothing sent.
      await tester.enterText(find.byKey(const Key('ai-purpose')), 'Hi');
      await tester.tap(find.byKey(const Key('ai-submit')));
      await tester.pumpAndSettle();
      expect(find.text(_en('calls.ai.tooShort')), findsOneWidget);
      expect(repo.aiRequests, isEmpty);

      await tester.enterText(find.byKey(const Key('ai-purpose')),
          'Confirm the delivery time on Thursday.');
      await tester.enterText(find.byKey(const Key('ai-script')),
          'Hello, this is a reminder about your delivery.');
      await tester.enterText(find.byKey(const Key('ai-seconds')), '2000');
      await tester.pump();
      await tester.ensureVisible(find.byKey(const Key('ai-submit')));
      await tester.tap(find.byKey(const Key('ai-submit')));
      await tester.pumpAndSettle();
      expect(repo.aiRequests.single, {
        'contact_id': 'c2',
        'purpose': 'Confirm the delivery time on Thursday.',
        'script': 'Hello, this is a reminder about your delivery.',
        'language': 'ar',
        'max_seconds': 900,
      });
      expect(find.text(_en('calls.ai.sent')), findsOneWidget);
      expect(
          find.text(_en('calls.ai.status.pending_approval')), findsOneWidget);
      await tester.tap(find.text(_en('calls.ai.openApprovals')).first);
      expect(approvals, 1);

      // A request still waiting can be cancelled.
      await tester.ensureVisible(find.byKey(const Key('ai-cancel-aicall_new')));
      await tester.tap(find.byKey(const Key('ai-cancel-aicall_new')));
      await tester.pumpAndSettle();
      expect(repo.cancelled, ['aicall_new']);
      expect(find.text(_en('calls.ai.cancelled')), findsOneWidget);
      expect(find.text(_en('calls.ai.status.cancelled')), findsOneWidget);
    });

    testWidgets('not configured: the provider and AI calls say what is missing',
        (tester) async {
      final repo = FakeCallsRepository();
      await _pump(tester, CallsController(repo));
      await openAi(tester);
      expect(find.text(_en('calls.provider.notConfigured')), findsOneWidget);
      expect(find.text(_en('calls.provider.askOwner')), findsOneWidget);
      expect(find.textContaining('AI calls need a connected voice provider'),
          findsOneWidget);
      expect(find.byKey(const Key('ai-form')), findsNothing);
      await _see(tester, find.text(_en('calls.ai.empty')));
      expect(find.text(_en('calls.ai.empty')), findsOneWidget);
    });

    testWidgets('an owner sees the provider account and its last check',
        (tester) async {
      final repo = FakeCallsRepository()
        ..aiReady = true
        ..owner = true;
      await _pump(tester, CallsController(repo));
      await openAi(tester);
      expect(find.text(_en('calls.provider.connected')), findsOneWidget);
      expect(find.text('Number: +971500000000'), findsOneWidget);
      expect(find.text(_en('calls.provider.aiOn')), findsOneWidget);
      expect(find.textContaining('Last check passed'), findsOneWidget);
      expect(find.text(_en('calls.provider.onWeb')), findsOneWidget);
    });

    testWidgets('viewers are told they cannot ask, and no form is shown',
        (tester) async {
      final repo = FakeCallsRepository()
        ..aiReady = true
        ..canLog = false
        ..aiItems = [_aiItem('aicall_1')];
      await _pump(tester, CallsController(repo));
      await openAi(tester);
      expect(find.text(_en('calls.ai.blocked.role')), findsOneWidget);
      expect(find.byKey(const Key('ai-form')), findsNothing);
      await _see(tester, find.byKey(const Key('ai-aicall_1')));
      expect(find.byKey(const Key('ai-cancel-aicall_1')), findsNothing);
    });

    testWidgets('no consenting contact leads to Contacts', (tester) async {
      final repo = FakeCallsRepository()
        ..aiReady = true
        ..empty = true;
      await _pump(tester, CallsController(repo));
      await openAi(tester);
      expect(find.text(_en('calls.ai.noConsenting')), findsOneWidget);
      await tester.tap(find.descendant(
          of: find.byType(AnumEmpty),
          matching: find.text(_en('calls.tab.contacts'))));
      await tester.pumpAndSettle();
      expect(find.text(_en('calls.contacts.empty')), findsOneWidget);
    });

    testWidgets('errors: a failed list retries, a failed provider says why',
        (tester) async {
      final repo = FakeCallsRepository()
        ..aiReady = true
        ..owner = true
        ..failAi = true
        ..failProvider = true;
      await _pump(tester, CallsController(repo));
      await openAi(tester);
      expect(find.textContaining("Couldn't load the provider"), findsOneWidget);
      await _see(tester, find.textContaining('AI calls are unavailable.'));
      await _see(tester, find.text(_en('calls.retry')).last);
      repo
        ..failAi = false
        ..failProvider = false
        ..aiItems = [_aiItem('aicall_1', status: 'failed')];
      await tester.ensureVisible(find.text(_en('calls.retry')).last);
      await tester.pumpAndSettle();
      await tester.tap(find.text(_en('calls.retry')).last);
      await tester.pumpAndSettle();
      expect(find.text(_en('calls.ai.status.failed')), findsOneWidget);
      expect(find.text('The contact withdrew consent.'), findsOneWidget);
    });

    testWidgets('a denied list explains who can see it', (tester) async {
      final repo = _DeniedAi()..aiReady = true;
      await _pump(tester, CallsController(repo));
      await openAi(tester);
      await _see(
          tester, find.text('Ask a workspace owner to make you a member.'));
    });
  });
}

/// The AI calls tab's own list (its first scrollable).
Finder _aiList() => find
    .descendant(
        of: find.byKey(const Key('ai-tab')), matching: find.byType(Scrollable))
    .first;

/// Scrolls the AI calls tab until [finder] is built and visible.
Future<void> _see(WidgetTester tester, Finder finder) =>
    tester.scrollUntilVisible(finder, 200, scrollable: _aiList());

class _DeniedAi extends FakeCallsRepository {
  @override
  Future<CallsPage<AiCallRequest>> aiCalls({int offset = 0}) async =>
      throw const ApiException(
          403, 'Ask a workspace owner to make you a member.');
}
