// The Contacts directory on the phone (docs/contacts.md#mobile): wire
// contract, search and paging, every state, the person page with the channel
// facts, marking and clearing do not contact (owners only), the API's 403,
// and layouts from 320 px at 200% text to 1440 px in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/features/contacts/contacts_controller.dart';
import 'package:anum_mobile/features/contacts/contacts_import.dart';
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

  final created = <JsonMap>[];
  final updated = <JsonMap>[];
  final proposalRows = <JsonMap>[
    {
      'id': 'prop_1',
      'reason': 'same_phone',
      'matched_value': '+971501234567',
      'status': 'open',
      'a': personJson('ct_sara', 'Sara Ahmed'),
      'b': personJson('ct_sarah', 'Sarah A.'),
      'created_at': '2026-11-10T09:30:00Z',
    }
  ];
  bool exportSaved = true;
  Object? exportError;
  bool mergeGone = false;

  @override
  Future<PersonDetail> create(JsonMap body) async {
    calls.add('POST person ${body['name']}');
    created.add(body);
    return PersonDetail.fromJson(JsonReader({
      ...detailJson(personJson('ct_new', body['name']! as String)),
      'proposals': [proposalRows.first],
    }));
  }

  @override
  Future<PersonDetail> update(String id, JsonMap body) async {
    calls.add('PATCH $id ${body['name']}');
    updated.add(body);
    return PersonDetail.fromJson(JsonReader(detailJson(
        {...personJson(id, body['name']! as String), 'notes': body['notes']})));
  }

  @override
  Future<void> delete(String id) async {
    calls.add('DELETE person $id');
    if (!owner) throw const ApiException(403, 'Only workspace owners');
  }

  @override
  Future<PersonDetail> merge(String keepId, String otherId) async {
    calls.add('MERGE $otherId into $keepId');
    if (mergeGone) throw const ApiException(404, 'Person not found');
    proposalRows.clear();
    return PersonDetail.fromJson(
        JsonReader(detailJson(personJson(keepId, 'Sara Ahmed'))));
  }

  @override
  Future<ProposalPage> proposals({int offset = 0}) async {
    calls.add('GET proposals offset=$offset');
    return ProposalPage.fromJson(JsonReader({
      'items': proposalRows,
      'total': proposalRows.length,
      'can_write': true,
    }));
  }

  @override
  Future<void> dismissProposal(String id) async {
    calls.add('DISMISS $id');
    proposalRows.removeWhere((p) => p['id'] == id);
  }

  @override
  Future<bool> exportCsv(String dialogTitle) async {
    calls.add('EXPORT');
    if (exportError != null) throw exportError!;
    return exportSaved;
  }

  /// The import's answers: the preview's rows, and what apply refuses.
  List<JsonMap> importRows = [
    {'row': 2, 'status': 'match', 'action': 'update', 'problems': <String>[]},
    {'row': 3, 'status': 'new', 'action': 'create', 'problems': <String>[]},
    {
      'row': 4,
      'status': 'invalid',
      'action': 'skip',
      'problems': [
        "'12' is not a phone number with its country code (+971...)."
      ]
    },
  ];
  Object? applyError;
  final importBodies = <JsonMap>[];

  @override
  Future<ImportPlan> previewImport(ImportFile file) async {
    importBodies.add(file.body());
    calls.add('PREVIEW ${file.format}');
    if (!owner) {
      throw const ApiException(403,
          'Only workspace owners import into the directory. Ask a workspace owner to import it.');
    }
    final counts = <String, int>{
      'rows': importRows.length,
      for (final s in const [
        'new',
        'match',
        'possible_duplicate',
        'duplicate_in_file',
        'invalid'
      ])
        s: importRows.where((r) => r['status'] == s).length,
    };
    return ImportPlan.fromJson(JsonReader({
      'columns': ['Full name', 'Mobile', 'Notes'],
      'mapping': importRows.isEmpty
          ? {
              'name': <String>[],
              'organisation': null,
              'notes': null,
              'phones': <String>[],
              'emails': <String>[],
              'do_not_contact': null
            }
          : {
              'name': ['Full name'],
              'organisation': null,
              'notes': 'Notes',
              'phones': ['Mobile'],
              'emails': <String>[],
              'do_not_contact': null
            },
      'suggested': true,
      'rows': importRows,
      'counts': counts,
      'fingerprint': 'f' * 64,
      'delimiter': ',',
    }));
  }

  @override
  Future<ImportOutcome> applyImport(ImportFile file, ImportPlan plan) async {
    calls.add('APPLY ${file.format} ${plan.fingerprint.length}');
    if (applyError != null) throw applyError!;
    return ImportOutcome.fromJson(JsonReader({
      'created': plan.create,
      'updated': plan.update,
      'skipped': plan.skip,
      'marks_added': 0,
      'proposals_created': 0,
      'rows': plan.create + plan.update + plan.skip,
    }));
  }
}

Future<void> pump(WidgetTester tester, FakeContacts repo,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en'),
    ContactsController? controller,
    ImportFilePicker? picker}) async {
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
        controller: controller ?? ContactsController(repo),
        repository: repo,
        importPicker: picker),
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
    await tester.scrollUntilVisible(find.text('Show more'), 200,
        scrollable: find
            .descendant(
                of: find.byType(ListView), matching: find.byType(Scrollable))
            .first);
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
    // Members edit and merge; only owners delete.
    expect(find.byKey(const Key('person-edit')), findsOneWidget);
    expect(find.byKey(const Key('person-delete')), findsNothing);
  });

  test('the person form is checked like the API', () {
    const l = AnumLocalizations(Locale('en'));
    final ok = personBody(const PersonDraft(
        name: '  Sara   Ahmed ',
        phones: '050 123 4567\n+971 4 555 0000',
        emails: 'SARA@tyre.example; ops@tyre.example'));
    expect(ok.body!['name'], 'Sara Ahmed');
    expect(ok.body!['phones'], ['050 123 4567', '+971 4 555 0000']);
    expect(ok.body!['emails'], ['SARA@tyre.example', 'ops@tyre.example']);
    expect(personBody(const PersonDraft()).error, 'contacts.err.name');
    final bad = personBody(const PersonDraft(name: 'X', phones: '12345'));
    expect(l.f(bad.error!, {'value': bad.value!}),
        '"12345" is not a phone number. Use the country code, for example +971501234567.');
    expect(personBody(const PersonDraft(name: 'X', emails: 'not-mail')).error,
        'contacts.err.email');
    expect(
        personBody(PersonDraft(
                name: 'X', phones: List.filled(11, '+971501234567').join('\n')))
            .error,
        'contacts.err.tooMany');
    final draft = PersonDraft.of(
        Person.fromJson(JsonReader(personJson('ct_sara', 'Sara Ahmed'))));
    expect(draft.phones, '050 123 4567');
    expect(draft.emails, 'sara@tyre.example');
  });

  testWidgets('Add person checks the form, saves and opens the person',
      (tester) async {
    final repo = FakeContacts();
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('contacts-add')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('person-save')));
    await tester.pumpAndSettle();
    expect(find.text('Give the person a name (up to 200 characters).'),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('person-name')), 'Nadia Kausar');
    await tester.enterText(
        find.byKey(const Key('person-phones')), '0501234567');
    await tester.tap(find.byKey(const Key('person-save')));
    await tester.pumpAndSettle();
    expect(repo.created.single['phones'], ['0501234567']);
    expect(
        find.textContaining('a possible duplicate was opened'), findsOneWidget);
    expect(find.byKey(const Key('channel-phone')), findsOneWidget);
  });

  testWidgets('Edit, Merge with and Delete on the person page', (tester) async {
    final repo = FakeContacts();
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('person-ct_sara')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('person-edit')));
    await tester.pumpAndSettle();
    expect(find.text('Edit Sara Ahmed'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('person-notes')), 'Calls after 4');
    await tester.tap(find.byKey(const Key('person-save')));
    await tester.pumpAndSettle();
    expect(repo.updated.single['notes'], 'Calls after 4');
    expect(repo.updated.single['phones'], ['050 123 4567']);
    expect(find.text('Calls after 4'), findsOneWidget);

    await tester.tap(find.byKey(const Key('person-merge')));
    await tester.pumpAndSettle();
    expect(find.text('Merge Sara Ahmed with another person'), findsOneWidget);
    await tester.tap(find.byKey(const Key('merge-pick-ct_omar')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('merge-confirm')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('MERGE ct_omar into ct_sara'));
    expect(find.text('Merged Omar Khan into Sara Ahmed.'), findsOneWidget);

    await tester.ensureVisible(find.byKey(const Key('person-delete')));
    await tester.tap(find.byKey(const Key('person-delete')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('person-delete-confirm')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('DELETE person ct_sara'));
    expect(find.byKey(const Key('contacts-search')), findsOneWidget);
  });

  testWidgets('possible duplicates: keep one, or different people',
      (tester) async {
    final repo = FakeContacts();
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('contacts-duplicates')));
    await tester.pumpAndSettle();
    expect(find.text('Same phone number: +971501234567'), findsOneWidget);
    await tester.tap(find.byKey(const Key('keep-prop_1-ct_sarah')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('MERGE ct_sara into ct_sarah'));
    expect(find.text('Merged Sara Ahmed into Sarah A..'), findsOneWidget);
    expect(find.text('No possible duplicates'), findsOneWidget);

    repo.proposalRows.add({
      'id': 'prop_2',
      'reason': 'same_email',
      'matched_value': 'ops@tyre.example',
      'status': 'open',
      'a': personJson('ct_a', 'Ali'),
      'b': personJson('ct_b', 'Alia'),
      'created_at': '2026-11-10T09:30:00Z',
    });
    await tester.drag(find.byType(ListView).first, const Offset(0, 300));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('dismiss-prop_2')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('DISMISS prop_2'));
    expect(find.text('Kept as different people.'), findsOneWidget);
  });

  testWidgets('owners export the directory as CSV; members do not see it',
      (tester) async {
    final repo = FakeContacts();
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('contacts-export')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('EXPORT'));
    expect(find.text('The CSV was saved.'), findsOneWidget);
    repo.exportError = const ApiException(403, 'Only workspace owners');
    await tester.tap(find.byKey(const Key('contacts-export')));
    await tester.pumpAndSettle();
    expect(find.text('Only workspace owners export the directory.'),
        findsOneWidget);
    await pump(tester, FakeContacts()..owner = false);
    expect(find.byKey(const Key('contacts-export')), findsNothing);
    expect(find.byKey(const Key('contacts-add')), findsOneWidget);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0]) {
    testWidgets('the form and duplicates fit at $width wide, 2x, Arabic',
        (tester) async {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        final repo = FakeContacts();
        await pump(tester, repo,
            size: Size(width, 900), textScale: 2, locale: locale);
        await tester.ensureVisible(find.byKey(const Key('contacts-add')));
        await tester.tap(find.byKey(const Key('contacts-add')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        Navigator.of(tester.element(find.byKey(const Key('person-save'))))
            .pop();
        await tester.pumpAndSettle();
        await tester
            .ensureVisible(find.byKey(const Key('contacts-duplicates')));
        await tester.tap(find.byKey(const Key('contacts-duplicates')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        if (locale.languageCode == 'ar') {
          expect(find.text('التكرارات المحتملة'), findsWidgets);
        }
      }
    });
  }

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

  test('an import file is read by its format and sent in its own field', () {
    expect(importFormatOf('People.XLSX'), 'xlsx');
    expect(importFormatOf('c.vcf'), 'vcf');
    expect(importFormatOf('a.txt'), 'csv');
    expect(importFormatOf('old.xls'), isNull);
    final csv = readImportFile(
        'p.csv', Uint8List.fromList(utf8.encode('Name\nRana\n')));
    expect(csv.body(), {'csv': 'Name\nRana\n', 'file_name': 'p.csv'});
    final vcf =
        readImportFile('c.vcf', Uint8List.fromList(utf8.encode('BEGIN:VCARD')));
    expect(vcf.body(),
        {'format': 'vcf', 'csv': 'BEGIN:VCARD', 'file_name': 'c.vcf'});
    final xlsx = readImportFile('p.xlsx', Uint8List.fromList([0x50, 0x4b]));
    expect(xlsx.body(),
        {'format': 'xlsx', 'file_base64': 'UEs=', 'file_name': 'p.xlsx'});
    for (final (name, bytes, code) in [
      ('old.xls', Uint8List(4), 'type'),
      ('p.csv', Uint8List(0), 'empty'),
      ('p.xlsx', Uint8List(importMaxWorkbookBytes + 1), 'too_large'),
      ('p.csv', Uint8List.fromList([0xff, 0xfe, 0x00]), 'not_text'),
    ]) {
      expect(
          () => readImportFile(name, bytes),
          throwsA(
              isA<FormatException>().having((e) => e.message, 'code', code)),
          reason: name);
    }
    for (final key
        in contactsEn.keys.where((k) => k.startsWith('contacts.import.'))) {
      expect(contactsAr[key], isNotNull, reason: key);
    }
  });

  testWidgets('owners import a file: pick, the preview summary, confirm, done',
      (tester) async {
    final repo = FakeContacts();
    final workbook = Uint8List.fromList([0x50, 0x4b, 0x03, 0x04]);
    await pump(tester, repo,
        size: const Size(800, 2400),
        picker: () async => (name: 'people.xlsx', bytes: workbook));
    await tester.ensureVisible(find.byKey(const Key('contacts-import')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('contacts-import')));
    await tester.pumpAndSettle();
    expect(find.text('Import people'), findsWidgets);
    await tester.ensureVisible(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    expect(repo.importBodies.single, {
      'format': 'xlsx',
      'file_base64': base64Encode(workbook),
      'file_name': 'people.xlsx'
    });
    expect(find.text('File: people.xlsx'), findsOneWidget);
    expect(find.text('What the import would do: 3 rows'), findsOneWidget);
    expect(find.text('Add 1, update 1, skip 1.'), findsOneWidget);
    expect(find.text('Name: Full name'), findsOneWidget);
    expect(find.text('Phone numbers: Mobile'), findsOneWidget);
    expect(find.textContaining("Row 4: '12' is not a phone number"),
        findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('contacts-import-apply')));
    await tester.tap(find.byKey(const Key('contacts-import-apply')));
    await tester.pumpAndSettle();
    expect(find.text('Import these people?'), findsOneWidget);
    await tester.tap(find.byKey(const Key('contacts-import-confirm')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('APPLY xlsx 64'));
    expect(find.textContaining('Imported: 1 added, 1 updated, 1 skipped'),
        findsOneWidget);
    // Back on the list, which loads again.
    final before = repo.calls.where((c) => c.startsWith('GET people')).length;
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(
        repo.calls.where((c) => c.startsWith('GET people')).length, before + 1);
  });

  testWidgets(
      'a refused file, a file whose columns ANUM cannot tell, a changed directory and a member',
      (tester) async {
    final repo = FakeContacts();
    var pick = (name: 'people.xls', bytes: Uint8List(4));
    await pump(tester, repo,
        size: const Size(800, 2400), picker: () async => pick);
    await tester.ensureVisible(find.byKey(const Key('contacts-import')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('contacts-import')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    expect(
        find.textContaining(
            'Choose a CSV (.csv), Excel (.xlsx) or vCard (.vcf) file.'),
        findsOneWidget);
    expect(repo.calls.where((c) => c.startsWith('PREVIEW')), isEmpty);
    // A file whose first row names nothing ANUM knows: the columns are chosen on the web.
    repo.importRows = [];
    pick =
        (name: 'odd.csv', bytes: Uint8List.fromList(utf8.encode('A,B\nx,y\n')));
    await tester.ensureVisible(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    expect(find.text('Choose the columns on the web'), findsOneWidget);
    expect(find.byKey(const Key('contacts-import-apply')), findsNothing);
    // The directory changed since the preview: the API's words, the preview kept.
    repo.importRows = [
      {'row': 2, 'status': 'new', 'action': 'create', 'problems': <String>[]}
    ];
    pick = (
      name: 'c.vcf',
      bytes:
          Uint8List.fromList(utf8.encode('BEGIN:VCARD\nFN:Rana\nEND:VCARD\n'))
    );
    repo.applyError = const ApiException(409,
        'Nothing was imported. Row 2: the person to update no longer holds this number. Preview the file again.');
    await tester.ensureVisible(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('contacts-import-choose')));
    await tester.pumpAndSettle();
    expect(repo.importBodies.last['format'], 'vcf');
    await tester.ensureVisible(find.byKey(const Key('contacts-import-apply')));
    await tester.tap(find.byKey(const Key('contacts-import-apply')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('contacts-import-confirm')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Nothing was imported. Row 2'), findsOneWidget);
    expect(find.byKey(const Key('contacts-import-apply')), findsOneWidget);

    // A member sees no Import people; an owner refused by the server is told who imports.
    final member = FakeContacts()..owner = false;
    await pump(tester, member);
    expect(find.byKey(const Key('contacts-import')), findsNothing);
  });

  testWidgets(
      'the import screen fits from 320 px at 200% text to 1440 px, English and Arabic',
      (tester) async {
    for (final (size, scale, locale) in [
      (const Size(320, 640), 2.0, const Locale('ar')),
      (const Size(390, 844), 1.0, const Locale('en')),
      (const Size(1440, 900), 1.0, const Locale('ar')),
    ]) {
      final repo = FakeContacts();
      await pump(tester, repo,
          size: size,
          textScale: scale,
          locale: locale,
          picker: () async => (
                name: 'people.csv',
                bytes: Uint8List.fromList(utf8.encode('Name\nA\n'))
              ));
      await tester.scrollUntilVisible(
          find.byKey(const Key('contacts-import')), 200,
          scrollable: find.byType(Scrollable).first);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('contacts-import')));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
          find.byKey(const Key('contacts-import-choose')), 200,
          scrollable: find.byType(Scrollable).last);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('contacts-import-choose')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull, reason: '$size $locale');
      await tester.scrollUntilVisible(
          find.byKey(const Key('contacts-import-counts')), 200,
          scrollable: find.byType(Scrollable).last);
      expect(tester.takeException(), isNull, reason: '$size $locale');
    }
  });
}
