// The Contacts directory on the phone (docs/contacts.md#mobile): wire
// contract, search and paging, every state, the person page with the channel
// facts, marking and clearing do not contact (owners only), the API's 403,
// and layouts from 320 px at 200% text to 1440 px in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/contacts/contacts_controller.dart';
import 'package:anum_mobile/features/contacts/contacts_models.dart';
import 'package:anum_mobile/features/contacts/contacts_repository.dart';
import 'package:anum_mobile/features/contacts/contacts_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/contacts_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

typedef JsonMap = Map<String, Object?>;

JsonMap personJson(String id, String name, {List<JsonMap> marks = const []}) =>
    {
      'id': id,
      'name': name,
      'organisation': id == 'ct_sara' ? 'Tyre Co' : '',
      'notes': id == 'ct_sara' ? 'Prefers Arabic' : '',
      'source': 'calls',
      'phones': [
        {'kind': 'phone', 'value': '+971501234567', 'label': '050 123 4567'}
      ],
      'emails': [
        {'kind': 'email', 'value': 'sara@tyre.example', 'label': ''}
      ],
      'sources': ['call_contact', 'business_contact'],
      'do_not_contact': marks,
      'open_proposals': 1,
      'created_by': 'member_a',
      'created_at': '2026-11-10T09:30:00Z',
      'updated_at': '2026-11-10T09:30:00Z',
    };

JsonMap detailJson(JsonMap person, {bool manage = true}) => {
      'person': person,
      'links': [
        {
          'id': 'l1',
          'kind': 'call_contact',
          'source_id': 'c1',
          'found': true,
          'title': 'Sara Ahmed',
          'detail': 'Tyre Co',
          'href': '#calls',
          'facts': <String, Object?>{}
        },
        {
          'id': 'l2',
          'kind': 'whatsapp_contact',
          'source_id': 'w1',
          'found': false,
          'title': '',
          'detail': '',
          'href': '',
          'facts': <String, Object?>{}
        },
      ],
      'channels': [
        for (final channel in ['phone', 'email', 'whatsapp'])
          {
            'channel': channel,
            'identifiers': [
              channel == 'email' ? 'sara@tyre.example' : '+971501234567'
            ],
            'do_not_contact': (person['do_not_contact'] as List)
                .cast<JsonMap>()
                .where((m) => m['channel'] == channel)
                .firstOrNull,
            'facts': [
              if (channel == 'phone')
                {
                  'source': 'calls',
                  'state': 'granted',
                  'detail': 'ai_calls',
                  'at': '2026-11-10T09:30:00Z'
                },
              if (channel == 'email')
                {
                  'source': 'business',
                  'state': 'granted',
                  'detail': 'contract',
                  'at': null
                },
              if (channel == 'whatsapp')
                {
                  'source': 'suppression',
                  'state': 'blocked',
                  'detail': 'marketing:+971•••4567',
                  'at': null
                },
            ],
            'can_contact': !(person['do_not_contact'] as List)
                .cast<JsonMap>()
                .any((m) => m['channel'] == channel || m['channel'] == 'all'),
          }
      ],
      'proposals': [],
      'mail_contacts': [],
      'can_write': true,
      'can_manage': manage,
    };

class FakeContacts implements ContactsRepository {
  bool denied = false, fail = false, empty = false, owner = true;
  int total = 2;
  final marks = <JsonMap>[];
  final calls = <String>[];

  @override
  Future<PeoplePage> people(
      {String query = '', int offset = 0, int limit = contactsPageSize}) async {
    calls.add('GET people q=$query offset=$offset');
    if (denied) {
      throw const ApiException(403,
          'Contacts hold personal data from Phone calls, Business, WhatsApp and the Email Center, so viewers cannot see them. Ask a workspace owner to make you a member.');
    }
    if (fail) throw const ApiException(500, 'Database unavailable');
    final items = empty
        ? <JsonMap>[]
        : offset > 0
            ? [personJson('ct_more', 'Person More')]
            : query.isEmpty
                ? [
                    personJson('ct_sara', 'Sara Ahmed', marks: marks),
                    personJson('ct_omar', 'Omar Khan', marks: [
                      {
                        'channel': 'whatsapp',
                        'reason': 'Asked to stop',
                        'set_by': 'owner_a',
                        'set_at': '2026-11-10T09:30:00Z'
                      }
                    ])
                  ]
                : [personJson('ct_sara', 'Sara Ahmed', marks: marks)];
    return PeoplePage.fromJson(JsonReader({
      'items': items,
      'total': empty ? 0 : (query.isEmpty ? total : 1),
      'counts': {
        'people': 2,
        'do_not_contact': 1,
        'open_proposals': 1,
        'by_source': {'calls': 2}
      },
      'can_write': true,
      'can_manage': owner,
      'can_export': owner,
    }));
  }

  @override
  Future<PersonDetail> person(String id) async {
    calls.add('GET person $id');
    if (denied) throw const ApiException(403, 'viewers cannot see them');
    if (id == 'missing') throw const ApiException(404, 'Person not found');
    return PersonDetail.fromJson(JsonReader(
        detailJson(personJson(id, 'Sara Ahmed', marks: marks), manage: owner)));
  }

  @override
  Future<void> markDoNotContact(
      String id, String channel, String reason) async {
    calls.add('PUT $id $channel $reason');
    marks.add({
      'channel': channel,
      'reason': reason,
      'set_by': 'user_local',
      'set_at': '2026-11-10T09:30:00Z'
    });
  }

  @override
  Future<void> clearDoNotContact(
      String id, String channel, String reason) async {
    calls.add('DELETE $id $channel $reason');
    if (!owner) {
      throw const ApiException(403,
          'Only workspace owners can do this (clear a do-not-contact mark, delete a person, export the directory).');
    }
    marks.removeWhere((m) => m['channel'] == channel);
  }
}

Future<void> pump(WidgetTester tester, FakeContacts repo,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en'),
    ContactsController? controller}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  // A new app each time, so a second pump starts the screen (and its navigator) afresh.
  await tester.pumpWidget(MaterialApp(
    key: UniqueKey(),
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: ContactsScreen(
        controller: controller ?? ContactsController(repo), repository: repo),
  ));
  await tester.pump();
  await tester.pump(const Duration(seconds: 1));
}

void main() {
  test('the wire types read the API and the strings match in both languages',
      () {
    final page = PeoplePage.fromJson(JsonReader({
      'items': [
        personJson('ct_sara', 'Sara Ahmed', marks: [
          {
            'channel': 'all',
            'reason': 'Left',
            'set_by': 'u',
            'set_at': '2026-11-10T09:30:00Z'
          }
        ])
      ],
      'total': 1,
      'counts': {
        'people': 1,
        'do_not_contact': 1,
        'open_proposals': 0,
        'by_source': <String, Object?>{}
      },
      'can_write': true,
      'can_manage': false,
      'can_export': false,
    }));
    expect(page.items.single.blockedChannels, ['phone', 'email', 'whatsapp']);
    expect(page.items.single.phones.single.label, '050 123 4567');
    final detail = PersonDetail.fromJson(
        JsonReader(detailJson(personJson('ct_sara', 'Sara Ahmed'))));
    expect(detail.channels.map((c) => c.canContact), [true, true, true]);
    expect(detail.channels.last.facts.single.bad, isTrue);
    expect(detail.links.last.found, isFalse);
    expect(reasonProblem('ab'), 'reason');
    expect(reasonProblem(' Asked by phone '), isNull);
    expect(contactsEn.keys.toSet(), contactsAr.keys.toSet());
    for (final value in [...contactsEn.values, ...contactsAr.values]) {
      expect(value.contains('—') || value.contains('–'), isFalse,
          reason: value);
    }
  });

  testWidgets(
      'the list shows everyone once with their marks, searches and pages',
      (tester) async {
    final repo = FakeContacts()..total = 30;
    await pump(tester, repo);
    expect(find.text('Sara Ahmed'), findsOneWidget);
    expect(find.text('+971501234567'), findsNWidgets(2));
    expect(find.text('Do not contact: WhatsApp'), findsOneWidget);
    expect(find.text('30 people'), findsOneWidget);
    expect(find.textContaining('2 people, 1 marked'), findsOneWidget);
    await tester.tap(find.text('Show more'));
    await tester.pumpAndSettle();
    expect(find.text('Person More'), findsOneWidget);
    expect(repo.calls, contains('GET people q= offset=2'));
    await tester.enterText(find.byKey(const Key('contacts-search')), 'sara');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.text('1 people match'), findsOneWidget);
    expect(find.text('Omar Khan'), findsNothing);
    expect(repo.calls.last, 'GET people q=sara offset=0');
  });

  testWidgets('empty, denied and failed states say what to do', (tester) async {
    await pump(tester, FakeContacts()..empty = true);
    expect(find.text('No people yet'), findsOneWidget);
    expect(find.textContaining('Rebuild now on the web'), findsOneWidget);
    await pump(tester, FakeContacts()..denied = true);
    expect(find.textContaining('viewers cannot see them'), findsOneWidget);
    final failing = FakeContacts()..fail = true;
    await pump(tester, failing);
    expect(find.textContaining('Database unavailable'), findsOneWidget);
    failing.fail = false;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('Sara Ahmed'), findsOneWidget);
  });

  testWidgets(
      'the person page shows the channel facts; members mark, owners clear',
      (tester) async {
    final repo = FakeContacts();
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('person-ct_sara')));
    await tester.pumpAndSettle();
    expect(find.text('Phone calls contact'), findsWidgets);
    expect(find.text('Deleted in its module'), findsOneWidget);
    expect(
        find.text('Phone calls: consent to AI calls recorded'), findsOneWidget);
    expect(find.text('Business: consent basis contract'), findsOneWidget);
    expect(find.textContaining('Opt-out list: marketing'), findsOneWidget);
    expect(find.text('May be contacted'), findsNWidgets(3));
    expect(find.text('Prefers Arabic'), findsOneWidget);
    // Mark e-mail with a reason; a short reason is refused in place.
    await tester.ensureVisible(find.byKey(const Key('mark-email')));
    await tester.tap(find.byKey(const Key('mark-email')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('dnc-confirm')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Give a reason of 3 to 300'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('dnc-reason')), 'Asked us to stop writing');
    await tester.tap(find.byKey(const Key('dnc-confirm')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('PUT ct_sara email Asked us to stop writing'));
    expect(find.text('Marked do not contact (E-mail).'), findsOneWidget);
    expect(
        find.textContaining(
            'Do not contact (E-mail): Asked us to stop writing'),
        findsOneWidget);
    expect(find.text('Blocked'), findsOneWidget);
    // Owners clear with a reason.
    await tester.ensureVisible(find.byKey(const Key('clear-email')));
    await tester.tap(find.byKey(const Key('clear-email')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('dnc-reason')), 'They asked for news');
    await tester.tap(find.byKey(const Key('dnc-confirm')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('DELETE ct_sara email They asked for news'));
    expect(find.text('Mark cleared (E-mail).'), findsOneWidget);
    expect(find.text('May be contacted'), findsNWidgets(3));
  });

  testWidgets(
      'members see who clears a mark and a failed call shows the API sentence',
      (tester) async {
    final repo = FakeContacts()..owner = false;
    repo.marks.add({
      'channel': 'phone',
      'reason': 'Asked',
      'set_by': 'owner_a',
      'set_at': '2026-11-10T09:30:00Z'
    });
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('person-ct_sara')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('clear-phone')), findsNothing);
    expect(find.text('Only workspace owners clear a mark.'), findsOneWidget);
    expect(find.text('Blocked'), findsOneWidget);
    expect(find.textContaining('on the web'), findsOneWidget);
  });

  testWidgets('layouts from 320 px at 200% text to 1440 px, English and Arabic',
      (tester) async {
    for (final locale in const [Locale('en'), Locale('ar')]) {
      for (final (size, scale) in [
        (const Size(320, 640), 2.0),
        (const Size(390, 844), 1.0),
        (const Size(1440, 900), 1.0)
      ]) {
        final repo = FakeContacts();
        await pump(tester, repo, size: size, textScale: scale, locale: locale);
        expect(tester.takeException(), isNull);
        expect(
            find.text(
                locale.languageCode == 'ar' ? 'جهات الاتصال' : 'Contacts'),
            findsWidgets);
        // The list is lazy: at 320 px and 200% text the rows start below the fold.
        await tester.scrollUntilVisible(
            find.byKey(const Key('person-ct_sara')), 200,
            scrollable: find
                .descendant(
                    of: find.byType(ListView),
                    matching: find.byType(Scrollable))
                .first);
        await tester.ensureVisible(find.byKey(const Key('person-ct_sara')));
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const Key('person-ct_sara')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.ensureVisible(find.byKey(const Key('channel-phone')));
        expect(find.byKey(const Key('channel-phone')), findsOneWidget);
      }
    }
  });
}
