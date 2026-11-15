// A loan's person from Contacts and the loans by person on the phone
// (docs/finance.md#people-from-contacts). Every name and figure is SYNTHETIC.

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/contacts/contacts_models.dart';
import 'package:anum_mobile/features/finance/finance_lending_strings.dart';
import 'package:anum_mobile/features/finance/finance_lending_view.dart';
import 'package:anum_mobile/features/finance/finance_loan_contacts.dart';
import 'package:anum_mobile/features/finance/finance_loan_contacts_strings.dart';
import 'package:anum_mobile/features/finance/finance_personal_models.dart';
import 'package:anum_mobile/features/finance/finance_personal_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, Object?> _person(String id, String name, [String org = '']) => {
      'id': id,
      'name': name,
      'organisation': org,
      'notes': '',
      'source': 'manual',
      'phones': const <Object?>[],
      'emails': const <Object?>[],
      'sources': const <Object?>[],
      'do_not_contact': const <Object?>[],
      'open_proposals': 0,
      'created_by': 'user_local',
      'created_at': '2026-11-01T00:00:00Z',
      'updated_at': '2026-11-01T00:00:00Z',
    };

Map<String, Object?> _loan(String id,
        {String person = 'Sara',
        String direction = 'lent',
        String? contactId,
        String? contactName,
        String? contactState,
        String outstanding = '300.00'}) =>
    {
      'id': id,
      'direction': direction,
      'person': person,
      'amount': '300.00',
      'currency': 'AED',
      'lent_on': '2026-10-01',
      'repaid': '0.00',
      'outstanding': outstanding,
      'status': 'open',
      'contact_id': contactId,
      'contact_name': contactName,
      'contact_state': contactState,
    };

class FakeSource implements LoanPeopleSource {
  FakeSource({this.denied = false, this.canWrite = true});
  bool denied, canWrite;
  final contacts = <Map<String, Object?>>[
    _person('contact_sara', 'Sara Ahmed', 'Example Co')
  ];
  final searched = <String>[];
  final added = <String>[];
  LoanPersonPage page = LoanPersonPage.fromJson(JsonReader({
    'items': [
      {
        'key': 'contact:contact_sara',
        'contact_id': 'contact_sara',
        'contact_state': 'linked',
        'name': 'Sara Ahmed',
        'names': ['Sara A.', 'Sara'],
        'loans': 2,
        'open_loans': 2,
        'overdue': 0,
        'owed': [
          {'currency': 'AED', 'they_owe_me': '300.00', 'i_owe': '200.00'}
        ],
        'last_on': '2026-10-05',
      }
    ],
    'total': 1,
  }));
  Object? peopleError;

  @override
  Future<PeoplePage> searchContacts(String query) async {
    searched.add(query);
    if (denied) throw const ApiException(403, 'Contacts hold personal data.');
    final items = contacts
        .where(
            (c) => '${c['name']}'.toLowerCase().contains(query.toLowerCase()))
        .toList();
    return PeoplePage.fromJson(JsonReader({
      'items': items,
      'total': items.length,
      'counts': {
        'people': items.length,
        'do_not_contact': 0,
        'open_proposals': 0
      },
      'can_write': canWrite,
      'can_manage': false,
    }));
  }

  @override
  Future<Person> addContact(String name) async {
    added.add(name);
    final made = _person('contact_${contacts.length + 1}', name);
    contacts.add(made);
    return Person.fromJson(JsonReader(made));
  }

  @override
  Future<LoanPersonPage> people({String query = '', int offset = 0}) async {
    if (peopleError != null) throw peopleError!;
    return query.isEmpty ? page : const LoanPersonPage(items: [], total: 0);
  }

  @override
  Future<LoanPersonDetail> person(String key) async =>
      LoanPersonDetail.fromJson(JsonReader({
        ...{
          'key': key,
          'contact_id': 'contact_sara',
          'contact_state': 'linked',
          'name': 'Sara Ahmed',
          'names': ['Sara A.', 'Sara'],
          'loans': 2,
          'open_loans': 2,
          'overdue': 0,
          'owed': const <Object?>[],
          'last_on': '2026-10-05',
        },
        'items': [
          _loan('loan_2',
              person: 'Sara A.',
              direction: 'borrowed',
              contactId: 'contact_sara',
              contactName: 'Sara Ahmed',
              contactState: 'linked',
              outstanding: '200.00'),
          _loan('loan_1',
              contactId: 'contact_sara',
              contactName: 'Sara Ahmed',
              contactState: 'linked'),
        ],
        'history': [
          {
            'loan_id': 'loan_1',
            'direction': 'lent',
            'currency': 'AED',
            'movement': {
              'link': {
                'id': 'link_1',
                'role': 'deposit',
                'amount': '100.00',
                'currency': 'AED',
                'status': 'active'
              },
              'posted_on': '2026-11-04',
              'description': 'From Sara',
              'account_id': null,
            },
          }
        ],
      }));
}

Widget _wrap(Widget child, {Locale locale = const Locale('en')}) => MaterialApp(
      theme: AnumTheme.dark(),
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: child,
    );

void main() {
  test('the catalogs match in English and Arabic', () {
    expect(FinanceLoanContactsStrings.catalogsMatch, isTrue);
  });

  test('a loan reads its contact; the typed name stays as history', () {
    final renamed = PersonLoan.fromJson(JsonReader(_loan('l1',
        contactId: 'c1',
        contactName: 'Sara Al Mansoori',
        contactState: 'linked')));
    expect(renamed.displayName, 'Sara Al Mansoori');
    expect(renamed.typedAs, 'Sara');
    final same = PersonLoan.fromJson(JsonReader(_loan('l2',
        person: 'sara  ahmed',
        contactId: 'c1',
        contactName: 'Sara Ahmed',
        contactState: 'linked')));
    expect(same.typedAs, isNull);
    final gone = PersonLoan.fromJson(
        JsonReader(_loan('l3', contactId: 'c1', contactState: 'deleted')));
    expect(gone.displayName, 'Sara');
    expect(loanContactNote(gone, const FinanceLoanContactsStrings(false)),
        'No longer in Contacts: the name saved on the loan is shown');
    final manual = PersonLoan.fromJson(JsonReader(_loan('l4')));
    expect(manual.contactId, isNull);
    expect(loanContactNote(manual, const FinanceLoanContactsStrings(false)),
        isNull);
  });

  test('the body sends contact_id only when the form had the picker', () {
    const s = FinancePersonalStrings(false);
    const l = FinanceLendingStrings(false);
    final values = <String, Object?>{
      'direction': 'lent',
      'person': 'Sara',
      'amount': '300',
      'lent_on': '2026-10-01',
    };
    final picked = loanInputFrom(values, s, l,
        editing: false, contact: const PickedContact(id: 'contact_sara'));
    expect(picked.toJson()['contact_id'], 'contact_sara');
    final cleared =
        loanInputFrom(values, s, l, editing: true, contact: PickedContact.none);
    expect(cleared.toJson().containsKey('contact_id'), isTrue);
    expect(cleared.toJson()['contact_id'], isNull);
    final without = loanInputFrom(values, s, l, editing: true);
    expect(without.toJson().containsKey('contact_id'), isFalse);
  });

  Future<(TextEditingController, ValueNotifier<PickedContact>)> pumpPicker(
      WidgetTester tester, FakeSource source,
      {Locale locale = const Locale('en')}) async {
    final person = TextEditingController();
    final picked = ValueNotifier(PickedContact.none);
    addTearDown(person.dispose);
    addTearDown(picked.dispose);
    await tester.pumpWidget(_wrap(
        Scaffold(
          body: ListView(children: [
            TextField(key: const Key('person'), controller: person),
            LoanContactPicker(source: source, person: person, picked: picked),
          ]),
        ),
        locale: locale));
    return (person, picked);
  }

  testWidgets(
      'typing searches Contacts and a tap links the person; unlink keeps the typed name',
      (tester) async {
    final source = FakeSource();
    final (person, picked) = await pumpPicker(tester, source);
    expect(find.textContaining('Type at least two letters'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('person')), 'Sara');
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pumpAndSettle();
    expect(source.searched, ['Sara']);
    await tester.tap(find.byKey(const Key('loan-contact-contact_sara')));
    await tester.pumpAndSettle();
    expect(picked.value.id, 'contact_sara');
    expect(find.text('Linked to Sara Ahmed in Contacts'), findsOneWidget);
    await tester.tap(find.byKey(const Key('loan-contact-unlink')));
    await tester.pumpAndSettle();
    expect(picked.value.id, isNull);
    expect(person.text, 'Sara');
  });

  testWidgets('nobody of that name: Add to Contacts creates and links them',
      (tester) async {
    final source = FakeSource();
    final (_, picked) = await pumpPicker(tester, source);
    await tester.enterText(
        find.byKey(const Key('person')), 'Example Neighbour');
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('Nobody called Example Neighbour'), findsOneWidget);
    await tester.tap(find.byKey(const Key('loan-contact-add')));
    await tester.pumpAndSettle();
    expect(source.added, ['Example Neighbour']);
    expect(picked.value.id, 'contact_2');
  });

  testWidgets(
      'without write access there is no Add; without read access the name stays typed',
      (tester) async {
    final source = FakeSource(canWrite: false);
    await pumpPicker(tester, source);
    await tester.enterText(find.byKey(const Key('person')), 'Nobody Here');
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('loan-contact-add')), findsNothing);
    expect(find.textContaining('Only workspace owners and members'),
        findsOneWidget);

    source.denied = true;
    await tester.enterText(find.byKey(const Key('person')), 'Cousin C');
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('loan-contact-denied')), findsOneWidget);
  });

  testWidgets('by person: the groups, then one person\'s loans and history',
      (tester) async {
    final source = FakeSource();
    await tester.pumpWidget(_wrap(LoanPeopleScreen(source: source)));
    await tester.pumpAndSettle();
    expect(find.text('Sara Ahmed'), findsOneWidget);
    expect(find.textContaining('2 open of 2'), findsOneWidget);
    await tester.tap(find.byKey(const Key('loan-person-contact:contact_sara')));
    await tester.pumpAndSettle();
    expect(find.text('Loans with Sara Ahmed'), findsOneWidget);
    expect(find.textContaining('Names saved on these loans: Sara A., Sara'),
        findsOneWidget);
    expect(find.byKey(const Key('loan-person-loan-loan_2')), findsOneWidget);
    expect(find.byKey(const Key('loan-person-event-link_1')), findsOneWidget);
  });

  testWidgets('by person: no match, error with retry, Arabic right to left',
      (tester) async {
    final source = FakeSource();
    await tester.pumpWidget(
        _wrap(LoanPeopleScreen(source: source), locale: const Locale('ar')));
    await tester.pumpAndSettle();
    expect(Directionality.of(tester.element(find.text('Sara Ahmed'))),
        TextDirection.rtl);
    await tester.enterText(find.byKey(const Key('loan-people-search')), 'zz');
    await tester.pump(const Duration(milliseconds: 350));
    await tester.pumpAndSettle();
    expect(find.text('لا أحد يطابق البحث'), findsOneWidget);

    source.peopleError =
        const ApiException(503, 'The database is not reachable.');
    await tester.enterText(find.byKey(const Key('loan-people-search')), '');
    await tester.pump(const Duration(milliseconds: 350));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('The database is not reachable.'), findsOneWidget);
    source.peopleError = null;
    await tester.tap(find.byWidgetPredicate((w) => w is OutlinedButton));
    await tester.pumpAndSettle();
    expect(find.text('Sara Ahmed'), findsOneWidget);
  });

  testWidgets('the picker fits a small phone with long names', (tester) async {
    tester.view.physicalSize = const Size(320 * 3, 640 * 3);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final source = FakeSource();
    source.contacts.add(_person(
        'contact_long',
        'Sara With A Very Long Family Name Indeed',
        'An Organisation With A Long Name'));
    await pumpPicker(tester, source);
    await tester.enterText(find.byKey(const Key('person')), 'Sara');
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}
